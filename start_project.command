#!/bin/zsh
cd -- "${0:A:h}"
if [[ ! -x .venv-ml/bin/python ]]; then
  print 'The project Python environment is missing. See README.md for setup.'
  read '?Press Return to close.'
  exit 1
fi
exec .venv-ml/bin/python frontend/launch.py
