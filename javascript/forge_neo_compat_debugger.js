(function () {
  const PREFIX = '[FNCDBG-BROWSER]';
  const NEEDLES = ['Try to reproduce', 'external software', 'None', 'Diffusers', 'ComfyUI', 'WebUI 1.5', 'InvokeAI', 'EasyDiffusion', 'DrawThings'];
  const seen = new WeakSet();
  function log(msg, payload) { try { console.log(PREFIX + ' ' + msg, payload || {}); } catch (_) {} }
  function textMatches(node) {
    const text = (node && node.innerText) || '';
    return NEEDLES.some(n => text.indexOf(n) !== -1);
  }
  function radioLabel(input) {
    const label = input.closest('label');
    if (label && label.innerText) return label.innerText.trim();
    const parent = input.parentElement;
    return parent && parent.innerText ? parent.innerText.trim() : input.value;
  }
  function attach(root) {
    try {
      const candidates = [];
      document.querySelectorAll('div,fieldset,label').forEach(el => { if (textMatches(el)) candidates.push(el); });
      candidates.forEach(el => {
        const scope = el.closest('div') || el;
        scope.querySelectorAll('input[type="radio"]').forEach(input => {
          if (seen.has(input)) return;
          seen.add(input);
          input.addEventListener('click', () => log('radio click detected', { label: radioLabel(input), checked: input.checked, inputName: input.name, value: input.value }), true);
          input.addEventListener('change', () => log('radio change detected', { label: radioLabel(input), checked: input.checked, inputName: input.name, value: input.value }), true);
          log('attached radio listener', { label: radioLabel(input), inputName: input.name, value: input.value });
        });
      });
    } catch (e) { log('attach failed', { error: String(e) }); }
  }
  log('loaded', { href: location.href });
  attach(document);
  const observer = new MutationObserver(() => attach(document));
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
