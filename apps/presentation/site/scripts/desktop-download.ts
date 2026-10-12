// Public build-time projection of scripts/desktop_release_artifacts.py's
// complete-set, manifest-last contract. This does not select updater channels.
const repository = "https://github.com/loopx-project/loopx";
const api = "https://api.github.com/repos/loopx-project/loopx/releases";
const manifestName = "DESKTOP-SHA256SUMS";
type Asset = { name: string; url: string; digest: string };
type Candidate = { version: string; parts: number[]; assets: Asset[]; manifest: Asset };
export type DesktopDownload = { version: string; url: string };

export function desktopArtifactNames(version: string): string[] {
  return ["LoopX.app.zip", `LoopX_${version}_aarch64.dmg`,
    `LoopX_${version}_x64-setup.exe`, `LoopX_${version}_x64_en-US.msi`].sort();
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" ? value as Record<string, unknown> : {};
}

function candidates(releases: unknown): Candidate[] {
  if (!Array.isArray(releases)) throw new Error("GitHub release inventory must be an array.");
  return releases.flatMap(value => {
    const release = record(value);
    const tag = typeof release.tag_name === "string" ? release.tag_name : "";
    const match = /^v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$/.exec(tag);
    if (!match || release.draft !== false || release.prerelease !== false || !Array.isArray(release.assets)) return [];
    const version = match[1];
    const parts = version.split(".").map(Number);
    if (!parts.every(Number.isSafeInteger)) return [];
    const names = [...desktopArtifactNames(version), manifestName];
    const assets: Asset[] = [];
    for (const name of names) {
      const matches = release.assets.map(record).filter(asset => asset.name === name);
      const asset = matches[0];
      const url = `${repository}/releases/download/${tag}/${name}`;
      if (matches.length !== 1 || asset.state !== "uploaded" || typeof asset.size !== "number" ||
          asset.size <= 0 || asset.browser_download_url !== url ||
          typeof asset.digest !== "string" || !/^sha256:[a-f0-9]{64}$/.test(asset.digest)) return [];
      assets.push({ name, url, digest: asset.digest.slice(7) });
    }
    return [{ version, parts, assets: assets.slice(0, -1), manifest: assets.at(-1)! }];
  }).sort((a, b) => b.parts[0] - a.parts[0] || b.parts[1] - a.parts[1] || b.parts[2] - a.parts[2]);
}

export async function resolveDesktopDownload(
  releases: unknown, readManifest: (url: string) => Promise<string>,
): Promise<DesktopDownload> {
  for (const candidate of candidates(releases)) {
    const body = await readManifest(candidate.manifest.url);
    const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body)))]
      .map(byte => byte.toString(16).padStart(2, "0")).join("");
    // Compare exact bytes/order with the canonical producer; no partial manifest.
    const expected = candidate.assets.map(asset => `${asset.digest}  ${asset.name}\n`).join("");
    if (hash !== candidate.manifest.digest || body !== expected) continue;
    return { version: candidate.version,
      url: candidate.assets.find(asset => asset.name === "LoopX.app.zip")!.url };
  }
  throw new Error("No complete stable desktop release is available; preserve the deployed site and retry publication.");
}

export async function fetchLatestDesktopDownload(
  { token, request = fetch }: { token?: string; request?: typeof fetch } = {},
): Promise<DesktopDownload> {
  const releases: unknown[] = [];
  const headers: Record<string, string> = { Accept: "application/vnd.github+json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  for (let page = 1; page <= 10; page++) {
    const response = await request(`${api}?per_page=100&page=${page}`,
      { headers, signal: AbortSignal.timeout(15_000) }).catch(cause => {
        throw new Error("GitHub release inventory request failed; retry the site build.", { cause });
      });
    if (!response.ok) throw new Error(`GitHub release inventory failed (HTTP ${response.status}); retry the site build.`);
    const rows: unknown = await response.json().catch(cause => {
      throw new Error("GitHub release inventory could not be read as JSON; retry the site build.", { cause });
    });
    if (!Array.isArray(rows)) throw new Error("GitHub release inventory must be an array.");
    releases.push(...rows);
    if (rows.length < 100) {
      return resolveDesktopDownload(releases, async url => {
        // Never forward the API token to GitHub asset redirects or visitor browsers.
        const manifest = await request(url, { signal: AbortSignal.timeout(15_000) }).catch(cause => {
          throw new Error("Desktop manifest request failed; retry the site build.", { cause });
        });
        if (!manifest.ok) throw new Error(`Desktop manifest download failed (HTTP ${manifest.status}); retry the site build.`);
        return manifest.text().catch(cause => {
          throw new Error("Desktop manifest body could not be read; retry the site build.", { cause });
        });
      });
    }
  }
  throw new Error("Release inventory exceeds 1,000 entries; increase the reviewed search bound before publishing.");
}
