const config = {
  branches: [
    { name: "main" },
    { name: "next" },
    { name: "+([0-9])?(.{+([0-9]),x}).x" },
    { name: "dev", prerelease: true },
    { name: "beta", prerelease: true },
    { name: "alpha", prerelease: true },
  ],
  plugins: [
    [
      "@semantic-release/commit-analyzer",
      {
        preset: "conventionalcommits",
      },
    ],
    [
      "@semantic-release/release-notes-generator",
      {
        preset: "conventionalcommits",
      },
    ],
    [
      "@semantic-release/changelog",
      {
        changelogTitle: "# Changelog",
      },
    ],
    [
      // Buf
      "@semantic-release/exec",
      {
        prepareCmd: [
          "buf mod update",
          "buf generate --include-imports buf.build/recap/arg-services",
          "find build/arg_services -type d -exec touch {}/__init__.py \\;",
          "cp -rf arg_services/ build/arg_services/",
        ].join(" && "),
      },
    ],
    [
      "@cihelper/semanticrelease-plugin-poetry",
      {
        publishPoetry: true,
      },
    ],
    [
      "@semantic-release/github",
      {
        assets: [
          { path: "dist/*.tar.gz", label: "sdist" },
          { path: "dist/*.whl", label: "wheel" },
        ],
        failComment: false,
        addReleases: "bottom",
      },
    ],
    [
      "@semantic-release/git",
      {
        message: "chore(release): ${nextRelease.version}",
        assets: ["pyproject.toml", "*/__init__.py", "CHANGELOG.md", "buf.lock"],
      },
    ],
  ],
};

module.exports = config;
