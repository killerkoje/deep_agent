"""`python -m deep_agent`.

The console script `deep-agent` is the normal entry point, but some
Windows machines have an Application Control policy that blocks the
generated .exe shim. Running the module directly sidesteps that with no
loss of function.
"""

from .cli import main

raise SystemExit(main())
