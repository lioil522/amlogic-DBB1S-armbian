# Input: GET /repos/{owner}/{repo}/releases. Arguments: release, edition.
[
  .[]
  | select(.tag_name | startswith("Armbian_" + $release + "_arm64_" + $edition + "_"))
  | .assets[]
  | select((.name | contains("-trunk_")) and (.name | endswith(".img.gz")))
]
| sort_by(.updated_at)
| reverse
| if length == 0 then error("No matching release/edition image")
  else .[0] | {date: .updated_at, url: .url, name: .name}
  end
