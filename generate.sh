#!/usr/bin/env sh

buf mod update &&
    buf generate --include-imports buf.build/recap/arg-services &&
    find src -type d -exec touch {}/__init__.py \; &&
    rm -f src/__init__.py &&
    cp -rf arg_services/ src/arg_services/
