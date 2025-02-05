import json
import statistics
from typing import Union
import uuid
from django.db import models
from django_fsm import FSMField, transition


from api.github_utils import get_github_repo
from users.models import User
from .constants import HOURS_TO_COMPLETE_CHOICES, UNSURE, TaskState


class PlanningPokerSession(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # github limits repos to 100 chars and org names to 40 chars
    repo_name = models.CharField(null=True, max_length=100)
    org_name = models.CharField(null=True, max_length=40)
    current_task = models.OneToOneField(
        "Task",
        on_delete=models.SET_NULL,
        verbose_name=("The task currently being estimated"),
        related_name="get_session",
        null=True,
    )

    moderator = models.ForeignKey(
        User, on_delete=models.SET_NULL, related_name="moderator_in", null=True,
    )
    voters = models.ManyToManyField(User, related_name="voter_in")

    def get_joining_details(self):
        return {
            "id": self.id, "repoName": self.repo_name
        }

    def get_current_task_idx(self):
        return self.tasks.filter(
            state=TaskState.FINISHED
        ).count()

    def get_next_task(self):
        return self.tasks.filter(
            state=TaskState.NOT_STARTED
        ).first()

    class Meta:
        db_table = "planningPokerSession"


class Task(models.Model):

    id = models.AutoField(primary_key=True)
    state = FSMField(
        default=TaskState.NOT_STARTED,
        verbose_name='State of the current round in the Planning Poker Session',
        choices=TaskState.CHOICES,
        protected=True,
    )
    title = models.CharField(max_length=256)
    description = models.CharField(max_length=65536, null=True, default="")

    github_issue_number = models.IntegerField(null=True)
    planning_poker_session = models.ForeignKey(
        "PlanningPokerSession",
        null=True,
        on_delete=models.CASCADE,
        related_name="tasks",
    )

    note = models.CharField(max_length=4000, null=True, blank=True)

    def get_vote_info(self):
        vote_values = self.votes.all()
        vote_descriptions = [
            str(vote)
            for vote in vote_values
        ]
        return vote_descriptions

    def get_stats(self):
        votes = self.votes.all()
        total_vote_count = len(votes)
        # filter unsure and unclear options and only keep regular i.e ones from 1 to 40
        numeric_votes = [
            vote.value for vote in votes if vote.value < UNSURE
        ]

        numeric_vote_count = len(numeric_votes)

        stats = {
            "total_vote_count": total_vote_count,
            "undecided_count": total_vote_count - numeric_vote_count,
            "mean": round(statistics.mean(numeric_votes), 3) if numeric_vote_count >= 1 else "not enough votes",
            "median": round(statistics.median(numeric_votes), 3) if numeric_vote_count >= 1 else "not enough votes",
            "std_dev": round(statistics.stdev(numeric_votes), 3)if numeric_vote_count >= 2 else "not enough votes",
        }

        return stats

    @transition(field=state, source=TaskState.NOT_STARTED, target=TaskState.VOTING)
    def start_round(self):
        pass

    @transition(field=state, source=TaskState.VOTING, target=TaskState.DISCUSSING)
    def start_discussion(self):
        pass

    @transition(field=state, source=TaskState.DISCUSSING, target=TaskState.VOTING)
    def replay_round(self):
        pass

    @transition(field=state, source=TaskState.DISCUSSING, target=TaskState.SAVING)
    def finish_discussion(self):
        pass

    @transition(field=state, source=TaskState.SAVING, target=TaskState.FINISHED)
    def save_round(self, note: str, label: Union[str, None] = None):

        curr_session = self.planning_poker_session
        repo_name = curr_session.repo_name
        org_name = curr_session.org_name
        user = curr_session.moderator
        issue_number = self.github_issue_number
        stats = self.get_stats()
        cols = stats.keys()

        # create markdown table from the stats of the round
        table_head = " | " + " | ".join(cols) + " | \n "
        separator = " | " + " | ".join(["---" for _ in cols]) + " | \n "
        table_body = " | " + " | ".join(map(str, stats.values())) + " | \n "

        comment = "## Planning Poker Stats: \n " + \
            table_head + separator + table_body + "## Notes \n " + note

        repo = get_github_repo(user, repo_name, org_name)
        issue = repo.get_issue(issue_number)
        issue.create_comment(comment)

        if label:
            issue.add_to_labels(label)

    @transition(field=state, source=TaskState.SAVING, target=TaskState.FINISHED)
    def skip_saving(self):
        pass

    def __str__(self):
        return self.title

    class Meta:
        db_table = "task"


class Vote(models.Model):
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="votes")

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="votes")

    value = models.PositiveSmallIntegerField(choices=HOURS_TO_COMPLETE_CHOICES)

    def __str__(self) -> str:
        return f'{self.user.name} voted: "{self.get_value_display()}"'
