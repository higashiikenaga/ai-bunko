// POST /api/submit … AIで書いた小説の投稿(自己申告制)。GitHub に直接コミットして公開する。
//
//   新しい作品: { mode: "new", title, author, genre, premise, ai_zone, ai_tools, completed,
//                chapter_title, body, declared: true }
//     → 作品IDと編集キー(1度だけ返す。サーバーにはSHA-256のハッシュだけ保存)を返す
//   続きの投稿: { mode: "append", novel, key, completed, chapter_title, body, declared: true }
//
// 「AIで書いたか」「区分が正しいか」は確かめられないので、申告(declared)への同意を必須にし、
// 違反は通報を受けて運営が novel.json の status を "hidden" にして非表示にする。
import { NOVEL_ID, db, jstDay, json, notConfigured, readBody, sameOrigin, voterHash } from "../../lib/db.js";
import { getFile, githubConfigured, putFile } from "../../lib/github.js";

const hex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
const randomHex = (n) => hex(crypto.getRandomValues(new Uint8Array(n)));
async function sha256(text) {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))));
}
function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}
// 制御文字を除き、改行をLFにそろえる
const clean = (v, max) => String(v ?? "").replace(/\r\n?/g, "\n").replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").trim().slice(0, max + 1);
const oneLine = (v, max) => clean(v, max).replace(/\s*\n\s*/g, " ");
const bad = (msg, status = 400) => json({ error: msg }, status);

export async function onRequestPost({ request, env }) {
  if (!githubConfigured(env)) return notConfigured();
  const DB = await db(env);
  if (!DB) return notConfigured();
  if (!sameOrigin(request)) return bad("不正なリクエストです", 403);

  const body = await readBody(request);
  if (!body) return bad("不正なリクエストです");
  const metaRes = await env.ASSETS.fetch(new URL("/data/meta.json", request.url));
  if (!metaRes.ok) return bad("サイトの設定を読み込めませんでした", 503);
  const meta = await metaRes.json();
  const R = meta.rules;

  if (body.declared !== true) return bad("AIで書いた作品であることの申告に同意してください");
  const chapterTitle = oneLine(body.chapter_title, R.chapter_title_max);
  const text = clean(body.body, R.chapter_max_chars);
  if (!chapterTitle || chapterTitle.length > R.chapter_title_max) return bad(`この話のタイトルを${R.chapter_title_max}文字以内で入力してください`);
  if (text.length < R.chapter_min_chars || text.length > R.chapter_max_chars) {
    return bad(`本文は${R.chapter_min_chars}〜${R.chapter_max_chars}文字で入力してください(現在${text.length}文字)`);
  }

  // 回数制限(1人1日あたりの話数)
  const voter = await voterHash(env, request, "submit");
  const day = jstDay();
  await DB.prepare("DELETE FROM submit_log WHERE day < ?1").bind(jstDay(3)).run();
  const used = (await DB.prepare("SELECT count FROM submit_log WHERE voter = ?1 AND day = ?2").bind(voter, day).first())?.count || 0;
  if (used >= R.daily_limit_per_person) return bad(`1日に投稿できるのは${R.daily_limit_per_person}話までです`, 429);

  const now = new Date().toISOString().replace(/\.\d+Z$/, "+00:00");
  let novelId, novel, sha, key = null;

  try {
    if (body.mode === "append") {
      novelId = String(body.novel || "");
      if (!NOVEL_ID.test(novelId)) return bad("作品IDが正しくありません");
      const file = await getFile(env, `content/novels/${novelId}/novel.json`);
      if (!file) return bad("作品が見つかりません", 404);
      novel = JSON.parse(file.text);
      sha = file.sha;
      if (!safeEqual(await sha256(String(body.key || "")), String(novel.edit_key_hash || ""))) return bad("編集キーが違います", 403);
      if (novel.status === "hidden") return bad("この作品は非表示になっています", 403);
      if (novel.status === "completed") return bad("完結済みの作品には続きを投稿できません", 400);
      if (novel.chapters.length >= R.max_chapters_per_work) return bad(`1作品の話数は${R.max_chapters_per_work}話までです`);
    } else if (body.mode === "new") {
      const title = oneLine(body.title, R.title_max);
      const author = oneLine(body.author, R.author_max);
      const premise = clean(body.premise, R.premise_max);
      const tools = oneLine(body.ai_tools, R.tools_max);
      const genre = String(body.genre || "");
      if (!title || title.length > R.title_max) return bad(`タイトルを${R.title_max}文字以内で入力してください`);
      if (!author || author.length > R.author_max) return bad(`ペンネームを${R.author_max}文字以内で入力してください`);
      if (!premise || premise.length > R.premise_max) return bad(`あらすじを${R.premise_max}文字以内で入力してください`);
      if (!tools || tools.length > R.tools_max) return bad("使用したAIの名前を入力してください");
      if (!meta.zones.some((z) => z.key === body.ai_zone)) return bad("AI生成の区分を選んでください");
      if (!meta.genres.includes(genre) && genre !== "その他") return bad("ジャンルが正しくありません");
      novelId = `${new Date().toISOString().slice(0, 10).replaceAll("-", "")}-${randomHex(3)}`;
      key = `ak_${randomHex(16)}`;
      novel = {
        id: novelId, title, author, genre, premise, status: "ongoing", ai_zone: body.ai_zone, ai_tools: tools,
        created_at: now, updated_at: now, edit_key_hash: await sha256(key), chapters: [],
      };
    } else {
      return bad("不正なリクエストです");
    }

    const index = novel.chapters.length + 1;
    const pad = String(index).padStart(3, "0");
    // 本文を先に、作品情報を後に書く(作品情報ができた時点で、本文は必ずある)
    await putFile(env, `content/novels/${novelId}/chapters/${pad}.md`, text + "\n", `投稿: ${novel.title} 第${index}話`);
    novel.chapters.push({ index, title: chapterTitle, word_count: text.length, created_at: now });
    novel.updated_at = now;
    if (body.completed === true) novel.status = "completed";
    await putFile(env, `content/novels/${novelId}/novel.json`, JSON.stringify(novel, null, 2) + "\n", `投稿: ${novel.title} 第${index}話`, sha);
  } catch (e) {
    return bad("保存に失敗しました。しばらくしてからやり直してください", 502);
  }

  await DB.prepare(
    `INSERT INTO submit_log (voter, day, count) VALUES (?1, ?2, 1) ON CONFLICT(voter, day) DO UPDATE SET count = count + 1`,
  ).bind(voter, day).run();
  return json({ id: novelId, url: `/novels/${novelId}/`, ...(key ? { key } : {}) });
}
