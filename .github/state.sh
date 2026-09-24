#!/usr/bin/env bash
# The private state repository on a GitHub runner (used by .github/workflows/run.yml).
#
#   bash .github/state.sh restore       clone it into ./state, unpack the bars from its
#                                       "bars" release, write config.local.yaml
#   bash .github/state.sh save          one fresh commit with no history, force-pushed,
#                                       and the bars back into the release
#   bash .github/state.sh save-token    only the TradingView token (a test run)
#
# Needs STATE_REPO (owner/name) and STATE_REPO_TOKEN (a key with Contents: read and
# write on that repository only). Bars live in a release asset, not in git: they are
# rewritten every day, and git would keep every version.
set -euo pipefail
: "${STATE_REPO:?}" "${STATE_REPO_TOKEN:?}"
url="https://x-access-token:${STATE_REPO_TOKEN}@github.com/${STATE_REPO}.git"
export GH_TOKEN="$STATE_REPO_TOKEN"

case "${1:-}" in
restore)
    git clone -q "$url" state
    git -C state config user.name "ta-screener bot"
    git -C state config user.email "actions@users.noreply.github.com"
    mkdir -p state/data state/logs
    cat > state/.gitignore <<'EOF'
# bars live in the "bars" release; a lock or temp file restored by git looks fresh
data/bars/*.parquet
*.lock
.lock
*.tmp
logs/screener.log.*
EOF
    if gh release download bars --repo "$STATE_REPO" --pattern bars.tar.zst \
            --dir "$RUNNER_TEMP" --clobber > /dev/null 2>&1; then
        tar --zstd -xf "$RUNNER_TEMP/bars.tar.zst" -C state/data
        echo "bars: restored ($(find state/data/bars -name '*.parquet' | wc -l) files)"
    else
        echo "bars: none saved yet (the first run fetches them all)"
    fi
    python .github/mask_tokens.py state/tv_tokens.json
    cat > config.local.yaml <<EOF
# Written by .github/state.sh on the runner (gitignored).
paths:
  data: "$PWD/state/data"
  logs: "$PWD/state/logs"
web:
  open_browser: false
live:
  update_after_close: false
EOF
    ;;
save)
    cd state
    ls -1t logs/tick-*.log 2> /dev/null | tail -n +15 | xargs -r rm --
    git checkout -q --orphan fresh
    git add -A
    git commit -qm "State as of $(date -u +%Y-%m-%dT%H:%MZ)"
    git push -q --force origin fresh:main
    echo "state: saved"
    if [ -d data/bars ]; then
        tar --zstd -cf "$RUNNER_TEMP/bars.tar.zst" -C data bars
        gh release view bars --repo "$STATE_REPO" > /dev/null 2>&1 \
            || gh release create bars --repo "$STATE_REPO" --title bars \
                   --notes "Daily bars for the runners (private)." > /dev/null
        gh release upload bars "$RUNNER_TEMP/bars.tar.zst" --repo "$STATE_REPO" --clobber
        echo "bars: saved ($(find data/bars -name '*.parquet' | wc -l) files)"
    fi
    ;;
save-token)
    cd state
    git add -- tv_tokens.json
    git commit -qm "token $(date -u +%Y-%m-%dT%H:%MZ)" || true
    git push -q origin HEAD:main
    echo "state: token saved"
    ;;
*)
    echo "usage: $0 restore|save|save-token" >&2
    exit 2
    ;;
esac
