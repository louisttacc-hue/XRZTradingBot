// Toasts, Dialoge und "Knopf beschäftigt"-Zustand. Alle Texte als textContent.

import { h, icon, button, clear } from './dom.js';

const TOAST_ICONS = { good: 'check', warn: 'alert', bad: 'alert', info: 'info', accent: 'sparkle', neutral: 'info' };
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function createUI({ toastRoot, modalRoot }) {
  // ------------------------------------------------------------------ Toasts
  function toast(text, tone = 'info', { title = '', timeout } = {}) {
    if (!text && !title) return null;
    const ms = timeout ?? (tone === 'bad' ? 10000 : tone === 'warn' ? 8000 : 5500);
    const el = h('div', { class: `toast tone-${tone}`, role: tone === 'bad' ? 'alert' : 'status' },
      h('div', { class: 'toast-icon' }, icon(TOAST_ICONS[tone] || 'info', 16)),
      h('div', { class: 'toast-body' },
        title ? h('div', { class: 'toast-title', text: title }) : null,
        text ? h('div', { class: 'toast-text', text }) : null),
      h('button', { type: 'button', class: 'toast-close', 'aria-label': 'Meldung schließen', onClick: () => close() }, icon('x', 14)));
    let timer = null;
    const close = () => {
      clearTimeout(timer);
      el.classList.add('is-leaving');
      setTimeout(() => el.remove(), 220);
    };
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(close, ms);
    };
    el.addEventListener('mouseenter', () => clearTimeout(timer));
    el.addEventListener('mouseleave', arm);
    toastRoot.prepend(el);
    while (toastRoot.children.length > 5) toastRoot.lastElementChild.remove();
    arm();
    return { close };
  }

  // ------------------------------------------------------------------ Dialog
  let open = null;

  /**
   * Allgemeiner Dialog. opts: {title, text, tone, icon, body: Node[], confirmLabel, cancelLabel,
   * input: {label, placeholder, expected, hint, value}} → Promise<{ok, value}>
   */
  function dialog(opts) {
    if (open) open.cancel();
    return new Promise((resolve) => {
      const previous = document.activeElement;
      const tone = opts.tone || 'accent';
      let inputEl = null;
      const confirmBtn = button(opts.confirmLabel || 'OK', { variant: tone === 'bad' ? 'danger' : 'primary' });
      const cancelBtn = opts.cancelLabel === null ? null : button(opts.cancelLabel || 'Abbrechen', { variant: 'ghost' });
      const expected = opts.input?.expected ? String(opts.input.expected).trim().toUpperCase() : '';
      const check = () => {
        if (!inputEl) return;
        const v = inputEl.value.trim();
        confirmBtn.disabled = expected ? v.toUpperCase() !== expected : v.length === 0 && opts.input.required !== false;
      };
      if (opts.input) {
        inputEl = h('input', {
          class: 'input input-lg',
          type: 'text',
          autocomplete: 'off',
          spellcheck: 'false',
          placeholder: opts.input.placeholder || '',
          value: opts.input.value || '',
          'aria-label': opts.input.label || opts.title,
        });
        inputEl.addEventListener('input', check);
        inputEl.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' && !confirmBtn.disabled) {
            ev.preventDefault();
            finish(true);
          }
        });
      }
      const titleId = `dlg-${Math.random().toString(36).slice(2, 8)}`;
      const box = h('div', { class: `dialog tone-${tone}`, role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId },
        h('div', { class: 'dialog-head' },
          h('div', { class: 'dialog-icon' }, icon(opts.icon || (tone === 'bad' ? 'alert' : 'info'), 20)),
          h('h2', { class: 'dialog-title', id: titleId, text: opts.title || '' })),
        opts.text ? h('p', { class: 'dialog-text', text: opts.text }) : null,
        opts.body || null,
        opts.input
          ? h('div', { class: 'dialog-input' },
            opts.input.label ? h('label', { class: 'field-label', text: opts.input.label }) : null,
            inputEl,
            opts.input.hint ? h('div', { class: 'field-hint', text: opts.input.hint }) : null)
          : null,
        h('div', { class: 'dialog-actions' }, cancelBtn, confirmBtn));
      const backdrop = h('div', { class: 'dialog-backdrop' }, box);
      if (inputEl?.previousElementSibling) inputEl.previousElementSibling.htmlFor = inputEl.id = `${titleId}-in`;

      const onKey = (ev) => {
        if (ev.key === 'Escape') {
          ev.preventDefault();
          finish(false);
        } else if (ev.key === 'Tab') {
          const items = [...box.querySelectorAll(FOCUSABLE)].filter((x) => x.offsetParent !== null);
          if (!items.length) return;
          const first = items[0];
          const last = items[items.length - 1];
          if (ev.shiftKey && document.activeElement === first) {
            ev.preventDefault();
            last.focus();
          } else if (!ev.shiftKey && document.activeElement === last) {
            ev.preventDefault();
            first.focus();
          }
        }
      };
      const finish = (ok) => {
        if (!open || open.box !== box) return;
        open = null;
        document.removeEventListener('keydown', onKey, true);
        backdrop.classList.add('is-leaving');
        setTimeout(() => backdrop.remove(), 180);
        modalRoot.classList.toggle('has-dialog', false);
        if (previous && typeof previous.focus === 'function') previous.focus();
        resolve({ ok, value: inputEl ? inputEl.value.trim() : '' });
      };
      confirmBtn.addEventListener('click', () => finish(true));
      cancelBtn?.addEventListener('click', () => finish(false));
      backdrop.addEventListener('mousedown', (ev) => {
        if (ev.target === backdrop) finish(false);
      });
      document.addEventListener('keydown', onKey, true);
      clear(modalRoot);
      modalRoot.append(backdrop);
      modalRoot.classList.toggle('has-dialog', true);
      open = { box, cancel: () => finish(false) };
      check();
      requestAnimationFrame(() => (inputEl || confirmBtn).focus());
    });
  }

  async function confirm(opts) {
    return (await dialog(opts)).ok;
  }

  async function prompt(opts) {
    const r = await dialog(opts);
    return r.ok ? r.value : null;
  }

  function info(title, text, tone = 'info') {
    return dialog({ title, text, tone, confirmLabel: 'Verstanden', cancelLabel: null });
  }

  /**
   * Führt eine asynchrone Aktion aus, zeigt den Knopf als beschäftigt und meldet Fehler als Toast.
   * Gibt das Ergebnis oder undefined (bei Fehler) zurück.
   */
  async function run(btn, fn, { errorTitle = 'Das hat nicht geklappt' } = {}) {
    if (btn) {
      if (btn.getAttribute('aria-busy') === 'true') return undefined;
      btn.setAttribute('aria-busy', 'true');
      btn.disabled = true;
    }
    try {
      return await fn();
    } catch (err) {
      toast(errorMessage(err), 'bad', { title: errorTitle });
      return undefined;
    } finally {
      if (btn) {
        btn.removeAttribute('aria-busy');
        btn.disabled = false;
      }
    }
  }

  return { toast, dialog, confirm, prompt, info, run };
}

export function errorMessage(err) {
  if (!err) return 'Unbekannter Fehler';
  if (typeof err === 'string') return err;
  const msg = err.detail || err.message || String(err);
  if (/failed to fetch|networkerror|load failed/i.test(msg)) return 'Keine Verbindung zum Vincent-Backend.';
  return msg;
}
