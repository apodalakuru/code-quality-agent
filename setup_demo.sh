#!/usr/bin/env bash
# One-time setup for the demo: Python deps + make demo-java-app its own git repo.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
source .venv/bin/activate
# --prefer-binary: newer cryptography releases may lack wheels for Intel macOS and
# would otherwise be built from source (needs Rust + OpenSSL headers).
pip install --prefer-binary -r requirements.txt

cd demo-java-app
if [ ! -d .git ]; then
  git init -b main
  printf '.DS_Store\ntarget/\n' > .gitignore
  git add .
  git -c user.name="demo" -c user.email="demo@example.com" commit -m "Seeded demo app with static-analysis findings"
fi
mvn -B -q test && echo "Demo app builds and its tests pass."

echo
echo "Next:  cp .env.example .env  (add ANTHROPIC_API_KEY), then:"
echo "       set -a; source .env; set +a; python -m agent.run"
