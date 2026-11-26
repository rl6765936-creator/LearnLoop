"""LearnLoop brain – design stage B0–B3 (see claude/brain-design.md).

No network, no real AI model, no database server in this stage:
- the model is a FakeModel behind ModelGateway,
- storage is an in-memory repository behind a Repository interface.
"""
