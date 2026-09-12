# Contributing

Use a separate Python 3.11 virtual environment and follow the README setup.
Keep real credentials, databases, generated decks, and user information out of
commits and issue attachments. Use fixtures and mocked providers for tests.

Before a pull request, run the verification commands in the README. Explain the
user-visible problem, the change, and the checks you ran. Add regression coverage
for behavior changes, especially authorization, legality, persistence, and
external contracts. Include screenshots for substantial UI changes using
synthetic data. Document limitations rather than claiming unmeasured improvements.

Keep changes focused. Schema changes should preserve existing records and include
an upgrade/rollback discussion. Network/model integrations must remain optional
for ordinary tests. Opening a PR does not deploy the hosted application.
