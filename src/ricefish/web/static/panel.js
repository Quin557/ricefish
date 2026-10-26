/* ricefish 面板的小交互：筛选、滑杆、行内保存。没有框架，失败就退回普通表单。 */
(function () {
  "use strict";

  function toast(text) {
    var el = document.getElementById("toast");
    if (!el) return;
    el.textContent = text;
    el.classList.add("show");
    window.clearTimeout(toast._timer);
    toast._timer = window.setTimeout(function () {
      el.classList.remove("show");
    }, 2200);
  }

  function bindFilter() {
    document.querySelectorAll("input[data-filter]").forEach(function (input) {
      var targetId = input.getAttribute("data-filter");
      var box = document.getElementById(targetId);
      if (!box) return;
      input.addEventListener("input", function () {
        var needle = input.value.trim().toLowerCase();
        box.querySelectorAll("[data-name]").forEach(function (row) {
          var hay = (row.getAttribute("data-name") || "").toLowerCase();
          row.style.display = !needle || hay.indexOf(needle) >= 0 ? "" : "none";
        });
      });
    });
  }

  function bindRanges() {
    document.querySelectorAll('input[type="range"][data-output]').forEach(function (range) {
      var out = document.getElementById(range.getAttribute("data-output"));
      var sync = function () {
        if (out) out.textContent = range.value;
      };
      range.addEventListener("input", sync);
      sync();
    });
    document.querySelectorAll("button[data-delta]").forEach(function (button) {
      button.addEventListener("click", function () {
        var form = button.closest("form");
        if (!form) return;
        var range = form.querySelector('input[type="range"]');
        if (!range) return;
        var delta = parseInt(button.getAttribute("data-delta") || "0", 10);
        var value = Math.max(0, Math.min(100, parseInt(range.value || "0", 10) + delta));
        range.value = String(value);
        range.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
  }

  function saveForm(form) {
    var state = form.querySelector(".save-state");
    if (state) state.textContent = "保存中…";
    var body = new URLSearchParams(new FormData(form));
    return fetch(form.action, {
      method: "POST",
      headers: { "X-Requested-With": "fetch", "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
      credentials: "same-origin"
    })
      .then(function (response) {
        return response.json().catch(function () {
          return { ok: response.ok };
        });
      })
      .then(function (data) {
        if (!data || data.ok === false) {
          throw new Error((data && data.error) || "保存失败");
        }
        if (state) state.textContent = "已保存";
        toast("已保存");
      })
      .catch(function (error) {
        if (state) state.textContent = "";
        toast(error.message || "保存失败");
      });
  }

  function bindQuickForms() {
    document.querySelectorAll("form[data-quick]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        if (!window.fetch || !window.URLSearchParams) return;
        event.preventDefault();
        saveForm(form);
      });
      var notes = form.querySelector('input[name="notes"], textarea[name="notes"]');
      if (notes) {
        notes.addEventListener("blur", function () {
          if (!window.fetch) return;
          saveForm(form);
        });
      }
    });
  }

  function bindTabs() {
    document.querySelectorAll("[data-tabs]").forEach(function (tabs) {
      var links = Array.prototype.slice.call(
        tabs.querySelectorAll("[data-tab-target]")
      );
      var panels = Array.prototype.slice.call(
        document.querySelectorAll(".tab-panel[data-tab]")
      );
      if (!links.length) return;
      var activate = function (key) {
        links.forEach(function (link) {
          link.classList.toggle("active", link.getAttribute("data-tab-target") === key);
        });
        panels.forEach(function (panel) {
          panel.style.display = panel.getAttribute("data-tab") === key ? "" : "none";
        });
      };
      links.forEach(function (link) {
        link.addEventListener("click", function (event) {
          event.preventDefault();
          activate(link.getAttribute("data-tab-target"));
        });
      });
      activate(links[0].getAttribute("data-tab-target"));
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindFilter();
    bindRanges();
    bindQuickForms();
    bindTabs();
  });
})();
