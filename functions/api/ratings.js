// GET /api/ratings … 全作品の人間による評価の平均と件数
import { json, loadAggs, summarize } from "../../lib/ratings.js";

export async function onRequestGet({ env }) {
  if (!env.RATINGS) return json({ error: "評価機能は未設定です" }, 503);
  const ratings = summarize(await loadAggs(env.RATINGS));
  return json({ ratings }, 200, { "cache-control": "public, max-age=30" });
}
