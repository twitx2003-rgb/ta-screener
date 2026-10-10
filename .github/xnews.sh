#!/usr/bin/env bash
# What the X news runs have seen (used by .github/workflows/xnews.yml): the read posts'
# ids and the time of the last search, on the "xnews" branch of the PRIVATE state
# repository, so a post is never sent twice.
#
#   bash .github/xnews.sh restore   the branch into ./xnews-state, its state.json into data/xnews
#   bash .github/xnews.sh keep      commit this run's state.json and push it
#   bash .github/xnews.sh bars      the stored bars into data/bars (the news pictures' charts)
#
# Needs STATE_REPO and STATE_REPO_TOKEN, like analyst.sh.
set -euo pipefail
: "${STATE_REPO:?}" "${STATE_REPO_TOKEN:?}"
url="https://x-access-token:${STATE_REPO_TOKEN}@github.com/${STATE_REPO}.git"

case "${1:-}" in
restore)
    mkdir -p data/xnews
    if git clone -q --depth 1 --branch xnews --single-branch "$url" xnews-state 2> /dev/null; then
        cp xnews-state/state.json data/xnews/state.json 2> /dev/null || true
        cp xnews-state/digest.json data/xnews/digest.json 2> /dev/null || true   # the morning digest's record
        echo "xnews state: restored"
    else
        git init -q -b xnews xnews-state
        git -C xnews-state remote add origin "$url"
        echo "xnews state: a new branch"
    fi
    git -C xnews-state config user.name "ta-screener bot"
    git -C xnews-state config user.email "actions@users.noreply.github.com"
    ;;
keep)
    if [ ! -f data/xnews/state.json ]; then echo "xnews state: nothing to keep"; exit 0; fi
    cp data/xnews/state.json xnews-state/state.json
    if [ -f data/xnews/digest.json ]; then cp data/xnews/digest.json xnews-state/digest.json; fi
    cd xnews-state
    git add -A
    git commit -qm "xnews $(date -u +%Y-%m-%dT%H:%MZ)" || { echo "xnews state: unchanged"; exit 0; }
    # one run at a time (the workflow's concurrency group), so this run's file is the newest
    if git push -q --force origin HEAD:xnews 2> /dev/null; then echo "xnews state: kept"; exit 0; fi
    echo "xnews state: could not push"
    exit 1
    ;;
bars)
    export GH_TOKEN="$STATE_REPO_TOKEN"
    tmp="${RUNNER_TEMP:-/tmp}"
    if gh release download bars --repo "$STATE_REPO" --pattern bars.tar.zst --dir "$tmp" --clobber > /dev/null 2>&1 \
            && mkdir -p data && tar --zstd -xf "$tmp/bars.tar.zst" -C data; then
        echo "bars: $(find data/bars -name '*.parquet' | wc -l) files (the news pictures' charts)"
    else
        echo "bars: not downloaded (the news pictures go without a chart)"
    fi
    ;;
*)
    echo "usage: $0 restore|keep|bars" >&2
    exit 2
    ;;
esac
