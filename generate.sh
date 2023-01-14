#!/usr/bin/env sh

buf mod update &&
    buf generate --include-imports buf.build/recap/arg-services &&
    find build/arg_services -type d -exec touch {}/__init__.py \; &&
    cp -rf arg_services/ build/arg_services/
