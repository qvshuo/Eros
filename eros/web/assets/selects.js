'use strict';
// Native selects hold form values; one accessible custom menu renders the options.
let openDropdown = null;
const dropdowns = new Map();

function closeDropdown(focus = false) {
  if (!openDropdown) return;
  const {
    button,
    menu
  } = openDropdown;
  menu.remove();
  button.setAttribute('aria-expanded', 'false');
  button.removeAttribute('aria-activedescendant');
  button.removeAttribute('aria-controls');
  openDropdown = null;
  if (focus) button.focus();
}

function refreshDropdowns() {
  for (const [select, button] of dropdowns) {
    const value = select.selectedOptions[0]?.textContent || '选择';
    button.title = select.selectedOptions[0]?.title || '';
    if (button.querySelector('span').textContent !== value) button.querySelector('span').textContent = value;
    button.disabled = select.disabled;
  }
}

function placeDropdown() {
  if (!openDropdown) return;
  const {
    button,
    menu
  } = openDropdown, rect = button.getBoundingClientRect();
  if (!button.getClientRects().length || rect.bottom < 0 || rect.top > innerHeight) {
    closeDropdown();
    return;
  }
  const width = Math.min(innerWidth - 16, Math.max(rect.width, 190));
  menu.style.width = width + 'px';
  menu.style.left = Math.min(innerWidth - width - 8, Math.max(8, rect.left)) + 'px';
  const natural = Math.min(520, menu.scrollHeight + 2, innerHeight - 16),
    below = innerHeight - rect.bottom - 13,
    above = rect.top - 13,
    down = below >= natural || below >= above,
    height = Math.min(natural, Math.max(40, down ? below : above));
  menu.style.maxHeight = height + 'px';
  menu.style.top = (down ? Math.min(innerHeight - height - 8, rect.bottom + 5) : Math.max(8, rect.top -
    height - 5)) + 'px';
}

function openSelect(select, button) {
  closeDropdown();
  const menu = document.createElement('div');
  menu.className = 'dropdown-menu';
  menu.setAttribute('role', 'listbox');
  menu.id = select.id + '-menu';
  button.setAttribute('aria-controls', menu.id);
  button.setAttribute('aria-expanded', 'true');
  const options = [...select.options].filter(option => !option.hidden);
  let index = Math.max(0, options.findIndex(option => option.selected));

  function highlight(value) {
    index = Math.max(0, Math.min(value, options.length - 1));
    for (let i = 0; i < menu.children.length; i++) {
      menu.children[i].classList.toggle('highlighted', i === index);
      menu.children[i].setAttribute('aria-selected', String(options[i].selected));
    }
    const option = menu.children[index];
    if (option) {
      button.setAttribute('aria-activedescendant', option.id);
      option.scrollIntoView({
        block: 'nearest'
      });
    }
  }

  function choose(value) {
    const option = options[value];
    if (!option || option.disabled) return;
    select.value = option.value;
    select.dispatchEvent(new Event('input', {
      bubbles: true
    }));
    select.dispatchEvent(new Event('change', {
      bubbles: true
    }));
    refreshDropdowns();
    closeDropdown(true);
  }
  for (const [i, option] of options.entries()) {
    const row = document.createElement('div');
    row.id = select.id + '-option-' + i;
    row.setAttribute('role', 'option');
    row.textContent = option.textContent;
    row.title = option.title || '';
    row.className = 'dropdown-option';
    if (option.disabled) row.setAttribute('aria-disabled', 'true');
    row.onpointermove = () => highlight(i);
    row.onclick = () => choose(i);
    menu.append(row);
  }
  document.body.append(menu);
  openDropdown = {
    button,
    menu,
    highlight,
    choose,
    get index() {
      return index;
    }
  };
  placeDropdown();
  highlight(index);
}

function initDropdowns() {
  for (const select of document.querySelectorAll('select')) {
    if (dropdowns.has(select)) continue;
    select.hidden = true;
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    const wrapper = document.createElement('span');
    wrapper.className = 'dropdown';
    select.before(wrapper);
    wrapper.append(select);
    const button = document.createElement('button');
    button.type = 'button';
    button.id = select.id + '-trigger';
    button.className = 'dropdown-trigger';
    button.setAttribute('role', 'combobox');
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    button.append(document.createElement('span'));
    const label = select.closest('label');
    if (label) label.htmlFor = button.id;
    const labelName = label?.querySelector('.sr-only')?.textContent.trim() || [...(label?.childNodes || [])]
      .filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim();
    button.setAttribute('aria-label', select.getAttribute('aria-label') || labelName || '选择');
    wrapper.append(button);
    dropdowns.set(select, button);
    button.onclick = () => openDropdown?.button === button ? closeDropdown(true) : openSelect(select, button);
    button.onkeydown = event => {
      if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) {
        event.preventDefault();
        if (openDropdown?.button !== button) {
          openSelect(select, button);
          return;
        }
        if (event.key === 'ArrowDown') openDropdown.highlight(openDropdown.index + 1);
        else if (event.key === 'ArrowUp') openDropdown.highlight(openDropdown.index - 1);
        else if (event.key === 'Home') openDropdown.highlight(0);
        else if (event.key === 'End') openDropdown.highlight(select.options.length - 1);
        else openDropdown.choose(openDropdown.index);
      } else if (event.key === 'Escape') {
        closeDropdown(true);
      } else if (event.key === 'Tab') {
        closeDropdown();
      }
    };
    select.addEventListener('change', refreshDropdowns);
  }
  refreshDropdowns();
}
document.addEventListener('pointerdown', event => {
  if (openDropdown && !openDropdown.button.contains(event.target) && !openDropdown.menu.contains(event
      .target)) closeDropdown();
});
window.addEventListener('scroll', placeDropdown, true);
window.addEventListener('resize', placeDropdown);
