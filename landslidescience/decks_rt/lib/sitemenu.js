/*
 * lib/sitemenu.js — the owner's menu on a deck served by the site (loaded by lib/deck.js when
 * deck.site.owner). A ☰ button at upper right that keeps out of the slide's way: it shows only
 * when the mouse is pushed against the right edge of the window (or leaves through it) or M
 * is pressed (M also opens and closes the menu), and goes a moment after the mouse has left
 * it. Until then it takes no clicks, so slide content under it works. While the editor is
 * open it sits at the right end of the editor's bar. The menu says what the deck is (draft, published, a copy) and offers what
 * can be done with it: edit, publish, copy, Practice (lib/practice.js), and the way to the list of talks, where the
 * actions that need a typed confirmation live (replace a published deck, discard a draft).
 * Actions are POSTs to /decks/_do with window.DECK_CSRF, the same ones the list page makes.
 */
(function () {
  "use strict";
  var site = window.DECK && window.DECK.site;
  if (!site || !site.owner) return;
  var css = document.createElement("style");
  css.textContent =
    ".kn-menu-btn{position:fixed;right:10px;top:8px;z-index:1500;width:34px;height:30px;border:0;border-radius:5px;background:rgba(0,0,0,.55);color:#fff;font:18px/30px -apple-system,Arial,sans-serif;cursor:pointer;opacity:0;pointer-events:none;transition:opacity .3s}" +
    ".kn-menu-edge{position:fixed;right:0;top:0;width:4px;height:100%;z-index:1499}body.kn-editing .kn-menu-edge{display:none}" +
    ".kn-menu-btn.on,body.kn-editing .kn-menu-btn{opacity:1;pointer-events:auto}" +
    "body.kn-editing .kn-menu-btn{top:3px;right:8px;width:30px;height:24px;font-size:15px;line-height:24px;background:#3a3a3c}" +
    "body.kn-editing .kn-ed-bar{padding-right:46px}" +
    ".kn-menu{position:fixed;right:10px;top:44px;z-index:1500;width:290px;padding:10px 0;border-radius:8px;background:#2a2a2d;color:#ddd;font:14px/1.45 -apple-system,'Helvetica Neue',Arial,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.5)}" +
    "body.kn-editing .kn-menu{top:34px}" +
    ".kn-menu .what{padding:2px 16px 10px;border-bottom:1px solid #444;margin-bottom:6px}.kn-menu .what b{color:#fff;display:block}.kn-menu .what span{color:#aaa;font-size:13px}" +
    ".kn-menu a,.kn-menu button.item{display:block;width:100%;box-sizing:border-box;padding:6px 16px;border:0;background:none;color:#eee;font:inherit;text-align:left;text-decoration:none;cursor:pointer}" +
    ".kn-menu a:hover,.kn-menu button.item:hover{background:#3a3a3e}.kn-menu .warn{color:#ff9d8a}" +
    ".kn-menu .ask{padding:6px 16px 10px;font-size:13px;color:#ccc}.kn-menu .ask input{width:100%;box-sizing:border-box;margin:6px 0;padding:4px 7px;font:inherit;background:#111;color:#eee;border:1px solid #666;border-radius:4px}" +
    ".kn-menu .ask button{font:inherit;padding:4px 12px;margin-right:6px;border-radius:4px;border:1px solid #666;background:#3a3a3c;color:#eee;cursor:pointer}.kn-menu .ask button.go{background:#2f6fd0;border-color:#2f6fd0}" +
    ".kn-menu .err{color:#ff8a80;padding:0 16px 6px;font-size:13px}";
  document.head.appendChild(css);

  function mk(tag, cls, parent, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    if (parent) parent.appendChild(n);
    return n;
  }
  var btn = mk("button", "kn-menu-btn", document.body, "☰"), menu = null, hide = 0;
  btn.title = "This talk: edit, publish, copy  (M)";
  function wake() {                                      // show the button; it goes once the mouse has left it alone
    btn.classList.add("on");
    clearTimeout(hide);
    hide = setTimeout(function () {
      if (menu || btn.matches(":hover") || edge.matches(":hover")) wake(); else btn.classList.remove("on");
    }, 1800);
  }
  // A strip along the right edge, above everything: over a live figure the mouse belongs to
  // the figure's page and this one hears nothing, and the strip is what still does.
  var edge = mk("div", "kn-menu-edge", document.body);
  edge.addEventListener("mouseenter", wake);
  edge.addEventListener("mousemove", wake);
  // In a window (not full screen) the mouse overshoots the strip and leaves: that counts too.
  document.documentElement.addEventListener("mouseleave", function (ev) { if (ev.clientX >= window.innerWidth - 2) wake(); });

  // one of the list page's actions; the server answers with a redirect to /decks/?msg=…[&bad=1]
  function act(fields) {
    var body = new URLSearchParams(fields);
    body.set("name", site.name);
    return fetch("/decks/_do", { method: "POST", body: body, headers: { "X-CSRFToken": window.DECK_CSRF || "" } }).then(function (r) {
      var u = new URL(r.url), msg = u.searchParams.get("msg");
      if (!r.ok) throw new Error("The site refused (" + r.status + "). Signed out? Reload and sign in.");
      if (u.searchParams.get("bad")) throw new Error(msg || "That did not work.");
      return msg;
    });
  }
  function close() { if (menu) menu.remove(); menu = null; }
  function open() {
    close();
    menu = mk("div", "kn-menu", document.body);
    var draft = site.state === "draft";
    var what = mk("div", "what", menu);
    mk("b", "", what, window.DECK.title || site.name);
    mk("span", "", what, draft ? "Draft: only you can open it" + (site.copy_of ? ". A copy of " + site.copy_of : "") : "Published: public, and not edited in place");
    var err = mk("div", "err", menu);
    function item(label, fn, cls) { var b = mk("button", "item" + (cls ? " " + cls : ""), menu, label); b.onclick = fn; return b; }
    function ask(text, input, go, fn) {                  // a second step in the menu's place, no browser dialog
      Array.prototype.forEach.call(menu.querySelectorAll(".item, a, .ask"), function (n) { n.remove(); });
      var box = mk("div", "ask", menu);
      mk("div", "", box, text);
      var f = input ? mk("input", "", box) : null;
      if (f) { f.placeholder = input; f.pattern = "[A-Za-z0-9][A-Za-z0-9_-]*"; f.focus(); }
      var ok = mk("button", "go", box, go);
      mk("button", "", box, "Cancel").onclick = open;
      function run() {
        if (f && !/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(f.value)) { err.textContent = "Letters, digits, - and _ only."; return; }
        err.textContent = ""; ok.disabled = true;
        fn(f && f.value).catch(function (e) { err.textContent = e.message; ok.disabled = false; });
      }
      ok.onclick = run;
      if (f) f.addEventListener("keydown", function (ev) { ev.stopPropagation(); if (ev.key === "Enter") run(); });
    }
    if (draft) {
      item(document.body.classList.contains("kn-editing") ? "Stop editing  (E)" : "Edit  (E)", function () {
        close();
        document.dispatchEvent(new KeyboardEvent("keydown", { key: "e", bubbles: true }));
      });
      item("Publish…", function () {
        ask("Publish “" + site.name + "”? Anyone with the address will be able to open it, and from then on it is constant: " +
            "changes are made through a copy.", null, "Publish", function () {
          return act({ action: "publish" }).then(function () { location.reload(); });
        });
      });
    }
    item(draft ? "Copy…" : "Copy to revise…", function () {
      ask(draft ? "A second draft, starting as this one is now. Name for the copy:" :
          "A draft copy to edit. Later, publish it as its own talk, or replace this one with it. Name for the copy:", "name_for_the_copy", "Copy", function (name) {
        return act({ action: "copy", "new": name }).then(function () { location.href = "/decks/" + name + "/"; });
      });
    });
    var pr = window.DECK_PRACTICE;                       // lib/practice.js: decks with a live map
    if (pr) {
      if (pr.on()) item("Finish practice…", function () { close(); pr.finish(); });
      else item("Practice for offline maps…", function () {
        ask("While Practice is on, everything the maps on your slides fetch is kept in this browser, so the talk can show the same views " +
            "with a poor connection or none. Go through the talk as you will give it, then Finish and choose what to keep (" + pr.limits + ").",
            null, "Start", function () { return pr.start().then(close); });
      });
      item("Saved map files…", function () { close(); pr.review(); });
    }
    if (draft && site.copy_of) mk("a", "warn", menu, "Replace “" + site.copy_of + "” with this…").href = "/decks/#danger-" + site.name;
    if (draft) mk("a", "warn", menu, "Discard this draft…").href = "/decks/#danger-" + site.name;
    mk("a", "", menu, "All talks").href = "/decks/";
  }
  btn.onclick = function () { if (menu) close(); else open(); btn.blur(); };
  document.addEventListener("pointerdown", function (ev) { if (menu && !menu.contains(ev.target) && ev.target !== btn) close(); }, true);
  document.addEventListener("keydown", function (ev) {
    if (menu && ev.key === "Escape") { close(); ev.stopImmediatePropagation(); return; }   // Escape closes the menu and nothing else
    var t = ev.target, typing = t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ""));
    if ((ev.key !== "m" && ev.key !== "M") || ev.metaKey || ev.ctrlKey || ev.altKey || typing) return;
    if (menu) close(); else { open(); wake(); }
  }, true);                                              // before the editor's and reveal's own key handlers
})();
