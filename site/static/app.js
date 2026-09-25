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

  // 展開予想: 人間の票(/api/predictions)と評価AIの票を棒グラフで出し、クリックで投票する
  async function setupPredictions(boxes) {
    let votes = null;
    try {
      const r = await fetch(root() + "api/predictions", { cache: "no-store" });
      if (r.ok) votes = (await r.json()).votes;
    } catch (e) { /* 集計が取れなくてもAIの票は表示する */ }
    const mine = load("aib-predict", {});
    boxes.forEach((box) => {
      const key = box.dataset.id + ":" + box.dataset.chapter;
      const ai = box.dataset.ai.split(",").map(Number);
      const msg = box.querySelector("[data-predict-msg]");
      const opts = [...box.querySelectorAll("[data-choice]")];
      function paint(human) {
        const ht = human.reduce((a, b) => a + b, 0), at = ai.reduce((a, b) => a + b, 0);
        opts.forEach((o, i) => {
          const hp = ht ? Math.round((100 * human[i]) / ht) : 0, ap = at ? Math.round((100 * ai[i]) / at) : 0;
          o.querySelector(".bar.human").style.width = hp + "%";
          o.querySelector(".bar.ai").style.width = ap + "%";
          o.querySelector(".predict-nums").textContent = `人間 ${hp}%(${human[i]}) ・ AI ${ap}%(${ai[i]})`;
          o.classList.toggle("mine", mine[key] === i);
        });
      }
      paint((votes && votes[key]) || [0, 0, 0]);
      if (!votes) { msg.textContent = "人間の投票は現在利用できません(評価AIの予想のみ表示)"; return; }
      opts.forEach((o, i) => o.addEventListener("click", async () => {
        msg.textContent = "送信中…";
        try {
          const r = await fetch(root() + "api/predict", {
            method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify({ novel: box.dataset.id, chapter: Number(box.dataset.chapter), choice: i }),
          });
          const d = await r.json();
          if (!r.ok) throw new Error(d.error || "投票できませんでした");
          mine[key] = i;
          save("aib-predict", mine);
          paint(d.counts);
          msg.textContent = "投票しました。答え合わせは次の話が出たあと";
        } catch (e) { msg.textContent = e.message; }
      }));
    });
  }

  // トップのスライド: 自動で送り、ドット・矢印・スワイプでも切り替えられる
  function setupSlider(root_) {
    const track = root_.querySelector(".slides"), slides = [...track.children];
    const dots = [...root_.querySelectorAll(".dots button")];
    if (slides.length < 2) return;
    let i = 0, timer;
    const go = (n) => { i = (n + slides.length) % slides.length; track.scrollTo({ left: slides[i].offsetLeft - track.offsetLeft, behavior: "smooth" }); };
    const paint = () => dots.forEach((d, k) => d.setAttribute("aria-current", k === i));
    track.addEventListener("scroll", () => { i = Math.round(track.scrollLeft / track.clientWidth); paint(); }, { passive: true });
    dots.forEach((d, k) => d.addEventListener("click", () => { go(k); restart(); }));
    root_.querySelector(".prev").addEventListener("click", () => { go(i - 1); restart(); });
    root_.querySelector(".next").addEventListener("click", () => { go(i + 1); restart(); });
    const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    function restart() { clearInterval(timer); if (!reduce) timer = setInterval(() => go(i + 1), 6000); }
    root_.addEventListener("mouseenter", () => clearInterval(timer));
    root_.addEventListener("mouseleave", restart);
    paint(); restart();
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-slider]").forEach(setupSlider);
    const preds = [...document.querySelectorAll("[data-predict]")];
    if (preds.length) setupPredictions(preds);
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
