// Capture trusted user gestures; Playwright tracing alone does not record manual input.
(() => {
  const send = payload => window.__testpilotRecord(payload).catch(() => {});
  const rootSelector = element => {
    const root = element.getRootNode();
    const unique = selector => root.querySelectorAll(selector).length === 1;
    let selector;
    for (const attribute of ['data-testid', 'id', 'name', 'aria-label']) {
      const value = element.getAttribute(attribute);
      if (!value) continue;
      const candidate = `[${attribute}=${JSON.stringify(value)}]`;
      if (unique(candidate)) { selector = candidate; break; }
    }
    if (!selector) {
      const parts = [];
      for (let node = element; node instanceof Element; node = node.parentElement) {
        const tag = node.localName;
        const siblings = node.parentElement ? [...node.parentElement.children].filter(item => item.localName === tag) : [node];
        parts.unshift(`${tag}:nth-of-type(${siblings.indexOf(node) + 1})`);
        if (unique(parts.join(' > '))) break;
      }
      selector = parts.join(' > ');
    }
    return root.host ? `${rootSelector(root.host)} >> ${selector}` : selector;
  };
  const inputVariable = element => {
    const descriptor = ['name', 'id', 'placeholder', 'autocomplete', 'aria-label'].map(name => element.getAttribute(name) || '').join(' ');
    if (element.matches('input[type=password]') || /password|secret|token|credential|api.?key|密码|密钥|令牌/i.test(descriptor)) return 'recorded_secret';
    if (/username|user.?name|account|login|账号|用户名|登录名/i.test(descriptor)) return 'recorded_username';
    if (/email|邮箱/i.test(descriptor)) return 'recorded_email';
    if (/phone|mobile|手机号|电话/i.test(descriptor)) return 'recorded_phone';
    return '';
  };
  const elementLabel = element => {
    const control = element.matches('input,textarea,select') ? element :
      element.closest('button,a,[role=button],[role=link],[role=checkbox],[role=radio],label') || element;
    const associatedLabels = control.labels ? [...control.labels].map(label => label.innerText || label.textContent || '') : [];
    const candidates = [control.getAttribute('aria-label'), control.getAttribute('placeholder'), control.getAttribute('title'),
      ...associatedLabels, control.innerText, control.textContent];
    const label = candidates.map(value => String(value || '').replace(/\s+/g, ' ').trim()).find(Boolean);
    return label ? label.slice(0, 160) : '';
  };
  const emit = (element, action, value) => {
    const payload = {action, selector: rootSelector(element), element_label: elementLabel(element)};
    if (value !== undefined) {
      const variable = inputVariable(element);
      payload.value = variable ? `{{${variable}}}` : String(value);
    }
    send(payload);
  };
  const target = event => event.composedPath().find(node => node instanceof Element);
  document.addEventListener('click', event => {
    if (!event.isTrusted) return;
    const element = target(event);
    if (!element) return;
    const label = element.closest('label');
    if (label?.control && ['checkbox', 'radio'].includes(label.control.type)) return;
    if (element.matches('input[type=checkbox],input[type=radio],select,option')) return;
    if (element.matches('input[type=file]')) { send({unsupported: '文件上传需要手动补充步骤'}); return; }
    emit(element.closest('button,a,[role=button]') || element, 'click');
  }, true);
  document.addEventListener('input', event => {
    if (!event.isTrusted || event.isComposing) return;
    const element = target(event);
    if (element.matches('input:not([type=checkbox]):not([type=radio]):not([type=file]),textarea') || element.isContentEditable) {
      emit(element, 'fill', element.isContentEditable ? element.textContent : element.value);
    }
  }, true);
  document.addEventListener('change', event => {
    if (!event.isTrusted) return;
    const element = target(event);
    if (element.matches('input[type=checkbox],input[type=radio]')) emit(element, 'check', element.checked);
    else if (element.matches('select')) {
      if (element.multiple) send({unsupported: '多选下拉框需要手动补充步骤'});
      else emit(element, 'select', element.value);
    }
  }, true);
  document.addEventListener('keydown', event => {
    if (!event.isTrusted || event.isComposing || !['Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    emit(target(event), 'press', `${event.shiftKey ? 'Shift+' : ''}${event.key}`);
  }, true);
  document.addEventListener('dblclick', event => {
    if (event.isTrusted) send({unsupported: '双击操作需要手动复核'});
  }, true);
  document.addEventListener('drop', event => {
    if (event.isTrusted) send({unsupported: '拖拽操作需要手动补充步骤'});
  }, true);
})();
