# Contributing

Thanks for looking. Tuckvault is a small tool and should stay one: a window,
a scheduler entry, an encrypted store. Changes that keep it small and make
it safer or clearer are the most welcome.

## Setting up

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m unittest discover -s tests
```

The tests run the real engine, storage program and window against a
temporary folder. They never touch your own Tuckvault settings, Task
Scheduler entries or the network.

## Before opening a pull request

- Add or update a test that fails without your change.
- `python -m unittest discover -s tests` and `python -m bandit -q -r tuckvault -ll` pass.
- `tuckvault/agent.py` stays standard-library only and must run on the
  Python 3 of an ordinary Linux machine; it is sent there as source.
- Anything that comes back from the backup destination is untrusted input.
  Validate it in `tuckvault/remote.py` before it is used.
- No new runtime dependency without a good reason; each one ships to every
  user and has to be audited.
- User-facing text is plain language: say what happened and what to do.

## Changing the storage format or the encryption

Open an issue first. Existing backups must stay readable, and changes
there need a second pair of eyes.

## Security problems

Please do not open a public issue. See [SECURITY.md](SECURITY.md).
