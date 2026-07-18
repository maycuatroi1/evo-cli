import rich_click as click

from evo_cli.commands.harness.check import check
from evo_cli.commands.harness.edit import debt, question, repo, step
from evo_cli.commands.harness.pull import pull
from evo_cli.commands.harness.serve import serve
from evo_cli.commands.harness.show import show
from evo_cli.commands.harness.views import graph, plans, repos, seams


@click.group("harness")
def harness_group():
    """Read and track a repo cluster: its manifest, its contract seams, its exec-plans.

    \b
    evo harness serve              dashboard with every dependency drawn as a DAG
    evo harness repos              repos in the manifest, with git state
    evo harness seams              contract seams: owner, consumers, verify command
    evo harness plans              progress across every exec-plan
    evo harness show <plan>        read one plan
    evo harness graph <plan>       print a DAG as an adjacency list
    evo harness check <plan>       check what the plan claims against real git
    evo harness step <plan> 3 done mark a step
    evo harness pull               fast-forward every repo
    """


for command in (serve, repos, seams, plans, show, graph, check, step, debt, question, repo, pull):
    harness_group.add_command(command)
