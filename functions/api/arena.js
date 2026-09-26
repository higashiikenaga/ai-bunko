// AI文庫アリーナ: 同じお題で2つのモデルが書いた掌編に、どちらが良いか投票する。
// GET  /api/arena                              … 対戦ごとの票数(サイトの組み立て時に Elo を計算する)
// POST /api/arena {"match", "choice"}         … 投票(choice: a / b / tie / bad。1対戦1人1票、つけ直し可)
import { db, json, notConfigured, readBody, sameOrigin, voterHash } from "../../lib/db.js";

const MATCH_ID = /^[0-9a-f]{10}$/;
const CHOICES = ["a", "b", "tie", "bad"];

async function tally(DB, match) {
  const { results } = await DB.prepare("SELECT choice, COUNT(*) AS c FROM arena_votes WHERE match = ?1 GROUP BY choice")
    .bind(match).all();
  const out = { a: 0, b: 0, tie: 0, bad: 0 };
  for (const r of results) if (r.choice in out) out[r.choice] = r.c;
  return out;
}

export async function onRequestGet({ env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  const { results } = await DB.prepare("SELECT match, choice, COUNT(*) AS c FROM arena_votes GROUP BY match, choice").all();
  const votes = {};
  for (const r of results) {
    votes[r.match] ??= { a: 0, b: 0, tie: 0, bad: 0 };
    votes[r.match][r.choice] = r.c;
  }
  return json({ votes }, 200, { "cache-control": "public, max-age=60" });
}

export async function onRequestPost({ request, env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  if (!sameOrigin(request)) return json({ error: "不正なリクエストです" }, 403);
  const body = await readBody(request);
  const match = String(body?.match || "");
  const choice = String(body?.choice || "");
  if (!MATCH_ID.test(match) || !CHOICES.includes(choice)) return json({ error: "不正なリクエストです" }, 400);
  // 公開中の対戦か確かめる
  const res = await env.ASSETS.fetch(new URL("/data/arena.json", request.url));
  if (!res.ok || !(await res.json()).some((m) => m.id === match)) return json({ error: "この対戦は見つかりません" }, 404);

  const voter = await voterHash(env, request, `arena:${match}`);
  await DB.prepare(
    `INSERT INTO arena_votes (match, voter, choice, updated_at) VALUES (?1, ?2, ?3, ?4)
     ON CONFLICT(match, voter) DO UPDATE SET choice = excluded.choice, updated_at = excluded.updated_at`,
  ).bind(match, voter, choice, Date.now()).run();
  return json({ counts: await tally(DB, match) });
}
