/**
 * Alpine factory + helpers for the trust popover.
 * Loaded from base.html. Depends on Alpine core only (no plugins).
 *
 * Behaviors:
 *   - Single source of truth for `open` state per popover.
 *   - Only one popover open at a time across the page.
 *   - Manual focus-trap: Tab cycles between focusable descendants of the card;
 *     focus is returned to the trigger on close.
 *
 * See docs/superpowers/specs/2026-05-10-trust-popups-design.md §4.
 */
(function () {
  const OPEN_EVENT = "popover:open";

  function focusableInside(card) {
    return Array.from(
      card.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
    );
  }

  window.popover = function popover() {
    return {
      open: false,
      _trigger: null,
      _card: null,
      _id: Math.random().toString(36).slice(2),

      init() {
        this._trigger = this.$el.querySelector(".popover-trigger");
        this._card = this.$el.querySelector(".popover-card");

        window.addEventListener(OPEN_EVENT, (e) => {
          if (e.detail && e.detail.id !== this._id && this.open) {
            this.open = false;
          }
        });

        this.$watch("open", (val) => {
          if (val) {
            window.dispatchEvent(
              new CustomEvent(OPEN_EVENT, { detail: { id: this._id } })
            );
            queueMicrotask(() => {
              const focusables = focusableInside(this._card);
              (focusables[0] || this._card).focus({ preventScroll: true });
            });
          } else {
            this._trigger && this._trigger.focus({ preventScroll: true });
          }
        });

        this.$el.addEventListener("keydown", (e) => {
          if (!this.open || e.key !== "Tab") return;
          const focusables = focusableInside(this._card);
          if (focusables.length === 0) return;
          const first = focusables[0];
          const last = focusables[focusables.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        });
      },
    };
  };
})();
