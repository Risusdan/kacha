// A fake browser for the DOM tests of the review page (review_page.test.mjs): the part of the DOM the page's modules
// use, a dialog and a video that only keep their state, sessionStorage, and timers that run only when a test moves
// the clock on. It is written by hand, so the tests need no npm package, and it is only as wide as the page needs: a
// selector it cannot read throws instead of matching nothing.
//
// What it does as a browser does, because the tests depend on it: an event bubbles from its target up to the
// document; click() on a disabled control does nothing; focus() on an element outside the page does nothing, and an
// element taken out of the page loses the focus (the body has it then); setting a field's value to another text puts
// the caret at its end. There is no layout: every size and position is 0. The elements of index.html are found by
// their ids and hang flat under the body, as nothing the tests do depends on how they nest.

// Every node of the page: Node is this class while the browser is installed (shared.js asks `instanceof Node`).
export class FakeNode {
  listeners = new Map();

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  fire(event) {
    for (const listener of this.listeners.get(event.type) ?? []) listener.call(this, event);
  }
}

export class FakeEvent {
  constructor(type, props = {}) {
    this.type = type;
    this.defaultPrevented = false;
    Object.assign(this, props);
  }

  preventDefault() {
    this.defaultPrevented = true;
  }
}

// ---- selectors: a list of compound selectors (tag, #id, .class, [name], [name="value"], [name=value]) --------------

const PART = /([a-z][a-z0-9]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=(?:"([^"]*)"|([\w-]+)))?\]/y;

function parse(selector) {
  return selector.split(",").map((text) => {
    const compound = text.trim();
    const checks = [];
    PART.lastIndex = 0;
    while (PART.lastIndex < compound.length) {
      const at = PART.lastIndex;
      const part = PART.exec(compound);
      if (part === null || part.index !== at) throw new Error(`the fake DOM cannot read the selector ${selector}`);
      const [, tag, id, name, attribute, quoted, bare] = part;
      if (tag) checks.push((node) => node.localName === tag);
      else if (id) checks.push((node) => node.id === id);
      else if (name) checks.push((node) => node.classList.contains(name));
      else {
        const value = quoted ?? bare;
        checks.push((node) => {
          const actual = node.getAttribute(attribute);
          return actual !== null && (value === undefined || actual === value);
        });
      }
    }
    if (checks.length === 0) throw new Error(`the fake DOM cannot read the selector ${selector}`);
    return checks;
  });
}

function matches(node, selector) {
  return parse(selector).some((checks) => checks.every((check) => check(node)));
}

// ---- elements ---------------------------------------------------------------------------------------------------

class ClassList {
  constructor(element) {
    this.element = element;
  }

  get names() {
    return this.element.className.split(/\s+/).filter(Boolean);
  }

  contains(name) {
    return this.names.includes(name);
  }

  add(...names) {
    this.element.className = [...new Set([...this.names, ...names])].join(" ");
  }

  remove(...names) {
    this.element.className = this.names.filter((name) => !names.includes(name)).join(" ");
  }

  toggle(name, force) {
    const on = force === undefined ? !this.contains(name) : Boolean(force);
    if (on) this.add(name);
    else this.remove(name);
    return on;
  }
}

const NO_BOX = Object.freeze({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 });

export class FakeElement extends FakeNode {
  id = "";
  className = "";
  dataset = {};
  style = { setProperty() {} };
  hidden = false;
  disabled = false;
  childNodes = []; // elements, and strings for text
  parentNode = null;
  texts = []; // every text set with textContent, in order: what a notice or the banner has shown
  scrollTop = 0;
  scrollLeft = 0;
  clientTop = 0;
  clientLeft = 0;
  clientWidth = 0;
  clientHeight = 0;
  offsetWidth = 0;
  offsetHeight = 0;
  #attributes = new Map();

  constructor(document, tag) {
    super();
    this.ownerDocument = document;
    this.localName = tag;
  }

  get classList() {
    return new ClassList(this);
  }

  // The element in a message, as `<button#approve.btn.primary>`. A test compares elements with this rather than
  // with assert.equal, whose message for two elements that differ prints each whole, with the page it hangs in: more
  // than node's memory holds.
  toString() {
    const classes = this.classList.names.map((name) => `.${name}`).join("");
    return `<${this.localName}${this.id ? `#${this.id}` : ""}${classes}>`;
  }

  get isConnected() {
    for (let node = this; node !== null; node = node.parentNode) if (node === this.ownerDocument.body) return true;
    return false;
  }

  get children() {
    return this.childNodes.filter((child) => child instanceof FakeElement);
  }

  get textContent() {
    return this.childNodes.map((child) => (child instanceof FakeElement ? child.textContent : child)).join("");
  }

  set textContent(text) {
    this.texts.push(String(text));
    this.replaceChildren(...(String(text) === "" ? [] : [String(text)]));
  }

  setAttribute(name, value) {
    this.#attributes.set(name, String(value));
  }

  getAttribute(name) {
    if (name.startsWith("data-")) {
      const value = this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())];
      return value === undefined ? null : String(value);
    }
    if (name === "id") return this.id || null;
    if (name === "class") return this.className || null;
    if (name === "hidden" || name === "disabled") return this[name] ? "" : null;
    return this.#attributes.get(name) ?? null;
  }

  // ---- the tree ----

  append(...nodes) {
    for (const node of nodes) {
      if (node instanceof FakeElement) {
        node.remove();
        node.parentNode = this;
        this.childNodes.push(node);
      } else {
        this.childNodes.push(String(node));
      }
    }
  }

  replaceChildren(...nodes) {
    for (const child of this.children) child.remove();
    this.childNodes = [];
    this.append(...nodes);
  }

  after(node) {
    node.remove();
    const siblings = this.parentNode.childNodes;
    siblings.splice(siblings.indexOf(this) + 1, 0, node);
    node.parentNode = this.parentNode;
  }

  remove() {
    if (this.parentNode === null) return;
    const siblings = this.parentNode.childNodes;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentNode = null;
    this.ownerDocument.lostFrom(this);
  }

  contains(node) {
    for (let at = node; at !== null && at !== undefined; at = at.parentNode) if (at === this) return true;
    return false;
  }

  matches(selector) {
    return matches(this, selector);
  }

  closest(selector) {
    for (let node = this; node !== null; node = node.parentNode) if (node.matches(selector)) return node;
    return null;
  }

  querySelectorAll(selector) {
    const found = [];
    const visit = (node) => {
      for (const child of node.children) {
        if (child.matches(selector)) found.push(child);
        visit(child);
      }
    };
    visit(this);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  // ---- events, focus and boxes ----

  dispatchEvent(event) {
    event.target = this;
    for (let node = this; node !== null; node = node.parentNode) node.fire(event);
    if (this.isConnected) this.ownerDocument.fire(event);
    return !event.defaultPrevented;
  }

  click() {
    if (this.disabled && (this.localName === "button" || this.localName === "input")) return;
    this.dispatchEvent(new FakeEvent("click"));
  }

  focus() {
    if (this.isConnected) this.ownerDocument.focused = this;
  }

  getBoundingClientRect() {
    return NO_BOX;
  }
}

class FakeInput extends FakeElement {
  selectionStart = 0;
  selectionEnd = 0;
  #value = "";

  get value() {
    return this.#value;
  }

  set value(text) {
    const value = String(text);
    if (value === this.#value) return;
    this.#value = value;
    this.setSelectionRange(value.length, value.length);
  }

  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }
}

class FakeDialog extends FakeElement {
  open = false;

  showModal() {
    this.open = true;
  }

  close() {
    this.open = false;
  }
}

class FakeVideo extends FakeElement {
  paused = true;
  currentTime = 0;
  playbackRate = 1;
  seeking = false;
  src = "";

  play() {
    this.paused = false;
    this.dispatchEvent(new FakeEvent("play"));
    return Promise.resolve();
  }

  pause() {
    if (this.paused) return;
    this.paused = true;
    this.dispatchEvent(new FakeEvent("pause"));
  }

  load() {}
}

const ELEMENTS = { input: FakeInput, dialog: FakeDialog, video: FakeVideo };

// ---- the document -------------------------------------------------------------------------------------------------

export class FakeDocument extends FakeNode {
  focused = null;
  #byId = new Map();

  // The document of `html` (index.html): its elements that have an id, under the body.
  constructor(html) {
    super();
    this.body = this.createElement("body");
    for (const [, tag, attributes] of html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/g)) {
      const id = /\sid="([^"]+)"/.exec(attributes)?.[1];
      if (id === undefined) continue;
      const node = this.createElement(tag);
      node.id = id;
      node.className = /\sclass="([^"]*)"/.exec(attributes)?.[1] ?? "";
      node.hidden = /\shidden\b/.test(attributes.replace(/"[^"]*"/g, '""')); // the attribute, not a word of a value
      this.body.append(node);
      this.#byId.set(id, node);
    }
  }

  get activeElement() {
    return this.focused ?? this.body;
  }

  getElementById(id) {
    return this.#byId.get(id) ?? null;
  }

  createElement(tag) {
    return new (ELEMENTS[tag] ?? FakeElement)(this, tag);
  }

  // `node` was taken out of the page: the focus in it goes to the body.
  lostFrom(node) {
    if (this.focused !== null && node.contains(this.focused)) this.focused = null;
  }
}

// ---- timers -----------------------------------------------------------------------------------------------------

// setTimeout and clearTimeout on a clock that stands still until advance() moves it on.
export class FakeClock {
  now = 0;
  #timers = new Map();
  #lastId = 0;

  setTimeout = (callback, ms = 0) => {
    this.#lastId += 1;
    this.#timers.set(this.#lastId, { at: this.now + Math.max(0, Number(ms) || 0), callback });
    return this.#lastId;
  };

  clearTimeout = (id) => {
    this.#timers.delete(id);
  };

  // Move the clock on by `ms`: each timer that falls due on the way runs at its time, in order, and what it starts
  // settles before the next one runs.
  async advance(ms) {
    const end = this.now + ms;
    for (;;) {
      const due = [...this.#timers].filter(([, timer]) => timer.at <= end)
        .sort(([a, x], [b, y]) => x.at - y.at || a - b);
      if (due.length === 0) break;
      const [id, timer] = due[0];
      this.#timers.delete(id);
      this.now = timer.at;
      timer.callback();
      await settle();
    }
    this.now = end;
  }
}

// Let everything the page waits for run: the fake answers of a test are promises resolved already, so one turn of the
// event loop (after every microtask) runs the page's awaits to their end.
export function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

// ---- installing it ----------------------------------------------------------------------------------------------

// Make the globals the page uses point at a new fake browser with the document of `html`, `fetch` as its fetch, a
// FakeClock for its timers and an empty sessionStorage. restore() puts the real globals back.
export function installBrowser({ html, fetch }) {
  const document = new FakeDocument(html);
  const clock = new FakeClock();
  const storage = new Map();
  const sessionStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => {
      storage.set(key, String(value));
    },
  };
  const globals = {
    document,
    window: { sessionStorage },
    Node: FakeNode,
    CSS: { escape: (text) => String(text) }, // the ids of the tests need no escaping
    ResizeObserver: class {
      observe() {}
    },
    fetch,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
  };
  const saved = new Map(Object.keys(globals).map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  for (const [name, value] of Object.entries(globals)) {
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  }
  return {
    document,
    clock,
    sessionStorage: storage,
    restore() {
      for (const [name, descriptor] of saved) {
        if (descriptor === undefined) delete globalThis[name];
        else Object.defineProperty(globalThis, name, descriptor);
      }
    },
  };
}
