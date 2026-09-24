#!/usr/bin/env bash
# The chart analyst on a GitHub runner (used by .github/workflows/analyst.yml).
#
#   bash .github/analyst.sh restore   the bars from the state repository's "bars" release,
#                                     its "analyses" branch into ./analyses, config.local.yaml
#   bash .github/analyst.sh keep      commit this run's analysis and log, and push them
#
# The nightly run (state.sh save) force-pushes the state repository's main branch only;
# the "analyses" branch is written here alone, by rebase-and-push. Needs STATE_REPO and
# STATE_REPO_TOKEN, like state.sh.
set -euo pipefail
: "${STATE_REPO:?}" "${STATE_REPO_TOKEN:?}"
url="https://x-access-token:${STATE_REPO_TOKEN}@github.com/${STATE_REPO}.git"
export GH_TOKEN="$STATE_REPO_TOKEN"

case "${1:-}" in
restore)
    mkdir -p data logs
    # The nightly save replaces the asset (--clobber), so a download can miss it briefly.
    for attempt in 1 2 3 4 5 6; do
        if gh release download bars --repo "$STATE_REPO" --pattern bars.tar.zst \
                --dir "$RUNNER_TEMP" --clobber > /dev/null 2>&1; then
            tar --zstd -xf "$RUNNER_TEMP/bars.tar.zst" -C data
            echo "bars: $(find data/bars -name '*.parquet' | wc -l) files"
            break
        fi
        if [ "$attempt" = 6 ]; then echo "bars: could not download them"; exit 1; fi
        sleep 15
    done
    if git clone -q --branch analyses --single-branch "$url" analyses 2> /dev/null; then
        echo "analyses: $(find analyses -mindepth 2 -maxdepth 2 -type d -name '[0-9]*' | wc -l) kept so far"
    else
        git init -q -b analyses analyses
        git -C analyses remote add origin "$url"
        echo "analyses: a new branch"
    fi
    git -C analyses config user.name "ta-screener bot"
    git -C analyses config user.email "actions@users.noreply.github.com"
    printf '*.png\n' > analyses/.gitignore
    cat > config.local.yaml <<EOF
# Written by .github/analyst.sh on the runner (gitignored).
paths:
  data: "$PWD/data"
  logs: "$PWD/logs"
EOF
    ;;
keep)
    if [ ! -d analyses/.git ]; then echo "analyses: no checkout, nothing to keep"; exit 0; fi
    mkdir -p analyses/logs
    cp logs/analyst-*.log analyses/logs/ 2> /dev/null || true
    cd analyses
    git add -A
    git commit -qm "analysis $(date -u +%Y-%m-%dT%H:%MZ)" || { echo "analyses: nothing new"; exit 0; }
    for attempt in 1 2 3; do
        git pull -q --rebase origin analyses 2> /dev/null || true
        if git push -q origin HEAD:analyses 2> /dev/null; then echo "analyses: kept"; exit 0; fi
        sleep 5
    done
    echo "analyses: could not push"
    exit 1
    ;;
*)
    echo "usage: $0 restore|keep" >&2
    exit 2
    ;;
esac
