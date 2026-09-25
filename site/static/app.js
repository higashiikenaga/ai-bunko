// ブックマーク・フォロー・しおりは、このブラウザの localStorage だけに保存する(サーバーには送らない)。
// ★評価と閲覧数だけは集計のためサイトのAPI(Cloudflare Pages Functions)に送る。API未設定の環境では何もしない。
(function () {
  const KEY = {
    bookmarks: "aibunko:bookmarks", follows: "aibunko:follows", progress: "aibunko:progress",
    ratings: "aibunko:myratings", viewed: "aibunko:viewed",
  };

  function load(key, fallback) {
    try {
      const v = JSON.parse(localStorage.getItem(key));
      return v == null ? fallback : v;
    } catch (e) {
      return fallback;
    }
  }
  function save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* プライベートモード等では保存できない */ }
  }

  const store = {
    // bookmarks: { 作品ID: { title, seenChapters } }  seenChapters = 最後に目次を見たときの話数(新着の判定用)
    bookmarks: () => load(KEY.bookmarks, {}),
    follows: () => load(KEY.follows, []),
    progress: () => load(KEY.progress, {}), // { 作品ID: 最後に読んだ章 }
    isBookmarked: (id) => id in store.bookmarks(),
    toggleBookmark(id, title, chapters) {
      const b = store.bookmarks();
      if (id in b) delete b[id]; else b[id] = { title, seenChapters: chapters };
      save(KEY.bookmarks, b);
    },
    markSeen(id, chapters) {
      const b = store.bookmarks();
      if (id in b) { b[id].seenChapters = chapters; save(KEY.bookmarks, b); }
    },
    isFollowing: (name) => store.follows().includes(name),
    toggleFollow(name) {
      const f = store.follows();
      save(KEY.follows, f.includes(name) ? f.filter((n) => n !== name) : f.concat([name]));
    },
    saveProgress(id, index) {
      const p = store.progress();
      p[id] = index;
      save(KEY.progress, p);
    },
  };
  window.AIBunko = store;

  // サイトのルートへの相対パス。自前のスタイルシートの場所から求める(Google Fonts など外部のCSSは除く)
  const root = () => document.querySelector('link[rel=stylesheet][href$="static/style.css"]').getAttribute("href").replace(/static\/style\.css$/, "");
  const today = () => new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);

  function refresh(btn) {
    const isBookmark = btn.dataset.kind === "bookmark";
    const on = isBookmark ? store.isBookmarked(btn.dataset.id) : store.isFollowing(btn.dataset.author);
    btn.classList.toggle("outline", !on);
    btn.textContent = isBookmark ? (on ? "★ ブックマーク済み" : "☆ ブックマーク") : (on ? "✓ フォロー中" : "＋ 作家をフォロー");
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  // 人間による閲覧を数える(同じ作品は1日1回だけ送る。サーバー側でも同じ人の重複は数えない)
  function countView(id) {
    const viewed = load(KEY.viewed, {});
    if (viewed[id] === today()) return;
    fetch(root() + "api/view", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ novel: id }),
    }).then((r) => {
      if (r.ok) {
        const v = load(KEY.viewed, {});
        for (const k of Object.keys(v)) if (v[k] !== today()) delete v[k];
        v[id] = today();
        save(KEY.viewed, v);
      }
    }).catch(() => {});
  }

  async function setupRating(box) {
    const id = box.dataset.id;
    const summary = box.querySelector("[data-rating-summary]");
    const msg = box.querySelector("[data-rating-msg]");
    const stars = [...box.querySelectorAll("[data-star]")];
    const mine = load(KEY.ratings, {});
    const row = document.querySelector("[data-human-summary-row]");
    const cell = document.querySelector("[data-human-summary]");

    const paint = (n) => stars.forEach((s) => { s.textContent = Number(s.dataset.star) <= n ? "★" : "☆"; });
    function show(r) {
      const text = r && r.count ? `★${r.avg}(${r.count}件)` : "まだありません";
      summary.textContent = text;
      if (cell) { cell.textContent = text; row.hidden = false; }
    }
    try {
      const res = await fetch(root() + "api/ratings", { cache: "no-store" });
      if (!res.ok) return;
      show((await res.json()).ratings[id]);
    } catch (e) {
      return;
    }
    box.hidden = false;
    paint(mine[id] || 0);
    stars.forEach((s) => {
      s.addEventListener("mouseenter", () => paint(Number(s.dataset.star)));
      s.addEventListener("mouseleave", () => paint(mine[id] || 0));
      s.addEventListener("click", async () => {
        const score = Number(s.dataset.star);
        msg.textContent = "送信中…";
        try {
          const res = await fetch(root() + "api/rate", {
            method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ novel: id, score }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || "送信できませんでした");
          mine[id] = score;
          save(KEY.ratings, mine);
          paint(score);
          show(data);
          msg.textContent = "評価しました";
        } catch (e) {
          msg.textContent = e.message;
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-kind]").forEach((btn) => {
      refresh(btn);
      btn.addEventListener("click", () => {
        if (btn.dataset.kind === "bookmark") store.toggleBookmark(btn.dataset.id, btn.dataset.title, Number(btn.dataset.chapters));
        else store.toggleFollow(btn.dataset.author);
        document.querySelectorAll("[data-kind]").forEach(refresh);
      });
    });
    const reading = document.querySelector("[data-reading]");
    if (reading) store.saveProgress(reading.dataset.id, Number(reading.dataset.index));
    const seen = document.querySelector("[data-seen]");
    if (seen) store.markSeen(seen.dataset.id, Number(seen.dataset.chapters));
    const view = document.querySelector("[data-view]");
    if (view) countView(view.dataset.id);
    const rating = document.querySelector("[data-human-rating]");
    if (rating) setupRating(rating);
  });
})();
