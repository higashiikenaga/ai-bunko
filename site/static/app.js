// ブックマーク・フォロー・しおり。すべてこのブラウザの localStorage だけに保存し、
// サーバーへは何も送らない(アカウントも不要)。
(function () {
  const KEY = { bookmarks: "aibunko:bookmarks", follows: "aibunko:follows", progress: "aibunko:progress" };

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
    // bookmarks: { novelId: { title, seenChapters } }  seenChapters = ブックマーク時点/最後に確認した話数
    bookmarks: () => load(KEY.bookmarks, {}),
    follows: () => load(KEY.follows, []),
    progress: () => load(KEY.progress, {}), // { novelId: 最後に読んだ章index }
    isBookmarked: (id) => id in store.bookmarks(),
    toggleBookmark(id, title, chapters) {
      const b = store.bookmarks();
      if (id in b) delete b[id]; else b[id] = { title, seenChapters: chapters };
      save(KEY.bookmarks, b);
      return id in b;
    },
    markSeen(id, chapters) {
      const b = store.bookmarks();
      if (id in b) { b[id].seenChapters = chapters; save(KEY.bookmarks, b); }
    },
    isFollowing: (name) => store.follows().includes(name),
    toggleFollow(name) {
      let f = store.follows();
      f = f.includes(name) ? f.filter((n) => n !== name) : f.concat([name]);
      save(KEY.follows, f);
      return f.includes(name);
    },
    saveProgress(id, index) {
      const p = store.progress();
      p[id] = index;
      save(KEY.progress, p);
    },
  };
  window.AIBunko = store;

  function refresh(btn) {
    const kind = btn.dataset.kind;
    const on = kind === "bookmark" ? store.isBookmarked(btn.dataset.id) : store.isFollowing(btn.dataset.author);
    btn.classList.toggle("secondary", on);
    btn.textContent = kind === "bookmark" ? (on ? "★ ブックマーク済み" : "☆ ブックマーク") : (on ? "フォロー中" : "＋ 作家をフォロー");
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-kind]").forEach((btn) => {
      refresh(btn);
      btn.addEventListener("click", () => {
        if (btn.dataset.kind === "bookmark") {
          store.toggleBookmark(btn.dataset.id, btn.dataset.title, Number(btn.dataset.chapters));
        } else {
          store.toggleFollow(btn.dataset.author);
        }
        document.querySelectorAll("[data-kind]").forEach(refresh);
      });
    });
    const reading = document.querySelector("[data-reading]");
    if (reading) {
      store.saveProgress(reading.dataset.id, Number(reading.dataset.index));
    }
    const seen = document.querySelector("[data-seen]");
    if (seen) {
      store.markSeen(seen.dataset.id, Number(seen.dataset.chapters));
    }
    const rating = document.querySelector("[data-human-rating]");
    if (rating) setupRating(rating);
  });

  // 人間による★評価。評価API(Cloudflare Pages Functions)が使えないとき(未設定・ローカル確認など)は表示しない
  async function setupRating(box) {
    const id = box.dataset.id;
    const summary = box.querySelector("[data-rating-summary]");
    const msg = box.querySelector("[data-rating-msg]");
    const stars = [...box.querySelectorAll("[data-star]")];
    const mine = load("aibunko:myratings", {});
    const root = document.querySelector('link[rel=stylesheet]').getAttribute("href").replace(/static\/style\.css$/, "");

    function paint(n) {
      stars.forEach((s) => { s.textContent = Number(s.dataset.star) <= n ? "★" : "☆"; });
    }
    function show(r) {
      summary.textContent = r && r.count ? `★${r.avg}(${r.count}件)` : "まだ評価がありません";
    }
    try {
      const res = await fetch(root + "api/ratings", { cache: "no-store" });
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
          const res = await fetch(root + "api/rate", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ novel: id, score }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || "送信できませんでした");
          mine[id] = score;
          save("aibunko:myratings", mine);
          paint(score);
          show(data);
          msg.textContent = "評価しました";
        } catch (e) {
          msg.textContent = e.message;
        }
      });
    });
  }
})();
