// POST /api/rate  { "novel": "20260925-173803", "score": 1〜5 } … 人間による★評価
import { NOVEL_ID, json, novelExists, rate } from "../../lib/ratings.js";

export async function onRequestPost({ request, env }) {
  if (!env.RATINGS) return json({ error: "評価機能は未設定です" }, 503);

  // 他のサイトから勝手に送信されないよう、同じオリジンからのリクエストだけ受け付ける
  const origin = request.headers.get("Origin");
  if (!origin || new URL(origin).host !== new URL(request.url).host) {
    return json({ error: "不正なリクエストです" }, 403);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "不正なリクエストです" }, 400);
  }
  const novel = String(body.novel || "");
  const score = Number(body.score);
  if (!NOVEL_ID.test(novel) || !Number.isInteger(score) || score < 1 || score > 5) {
    return json({ error: "評価は1〜5の整数で送ってください" }, 400);
  }
  if (!(await novelExists(env, request.url, novel))) {
    return json({ error: "作品が見つかりません" }, 404);
  }
  return json(await rate(env, request, novel, score));
}
