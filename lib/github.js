// GitHub の Contents API でリポジトリにファイルをコミットする(投稿された作品を content/novels/ に保存する)。
// 必要な設定(Cloudflare Pages の環境変数・シークレット):
//   GITHUB_TOKEN  … このリポジトリの Contents への書き込み権限だけを持つ fine-grained トークン(シークレット)
//   GITHUB_REPO   … "owner/repo"
//   GITHUB_BRANCH … 省略時は main
// コミットで Pages の Git ビルドが走り、数分で公開される。

const API = "https://api.github.com";

export const githubConfigured = (env) => !!(env.GITHUB_TOKEN && env.GITHUB_REPO);

function headers(env) {
  return {
    authorization: `Bearer ${env.GITHUB_TOKEN}`,
    accept: "application/vnd.github+json",
    "user-agent": "ai-bunko-submit",
    "x-github-api-version": "2022-11-28",
    "content-type": "application/json",
  };
}

const b64 = (text) => {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
};
const unb64 = (b) => new TextDecoder().decode(Uint8Array.from(atob(b.replace(/\n/g, "")), (c) => c.charCodeAt(0)));
const branch = (env) => env.GITHUB_BRANCH || "main";
const url = (env, path) => `${API}/repos/${env.GITHUB_REPO}/contents/${path.split("/").map(encodeURIComponent).join("/")}`;

// ファイルを読む。なければ null
export async function getFile(env, path) {
  const res = await fetch(`${url(env, path)}?ref=${encodeURIComponent(branch(env))}`, { headers: headers(env) });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GitHub GET ${res.status}`);
  const data = await res.json();
  return { sha: data.sha, text: unb64(data.content) };
}

// ファイルを作る/更新する(更新は sha が必要)
export async function putFile(env, path, text, message, sha) {
  const res = await fetch(url(env, path), {
    method: "PUT",
    headers: headers(env),
    body: JSON.stringify({ message, content: b64(text), branch: branch(env), ...(sha ? { sha } : {}) }),
  });
  if (!res.ok) throw new Error(`GitHub PUT ${res.status}`);
}
