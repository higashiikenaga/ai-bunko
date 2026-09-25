// POST /api/view  { "novel": "20260925-173803" } … 人間による閲覧を1件数える(同じ人・同じ作品は1日1回まで)
import { NOVEL_ID, db, jstDay, json, notConfigured, novelExists, readBody, sameOrigin, voterHash } from "../../lib/db.js";

export async function onRequestPost({ request, env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  if (!sameOrigin(request)) return json({ error: "不正なリクエストです" }, 403);

  const body = await readBody(request);
  const novel = String(body?.novel || "");
  if (!NOVEL_ID.test(novel)) return json({ error: "不正なリクエストです" }, 400);
  if (!(await novelExists(env, request.url, novel))) return json({ error: "作品が見つかりません" }, 404);

  const day = jstDay();
  const voter = await voterHash(env, request, novel);
  const seen = await DB.prepare("INSERT OR IGNORE INTO view_seen (novel, voter, day) VALUES (?1, ?2, ?3)")
    .bind(novel, voter, day).run();
  if (seen.meta.changes > 0) {
    await DB.batch([
      DB.prepare(
        `INSERT INTO views (novel, day, count) VALUES (?1, ?2, 1)
         ON CONFLICT(novel, day) DO UPDATE SET count = count + 1`,
      ).bind(novel, day),
      // 二重カウント防止用の記録は2日で消す(個人の閲覧履歴を残さない)
      DB.prepare("DELETE FROM view_seen WHERE day < ?1").bind(jstDay(2)),
    ]);
  }
  return json({ counted: seen.meta.changes > 0 });
}
