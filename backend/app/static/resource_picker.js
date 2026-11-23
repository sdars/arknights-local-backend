(function () {
  function label(item) {
    const wrap = document.createElement("div");
    wrap.className = "resource-name";
    wrap.textContent = item.name || item.id;
    const id = document.createElement("span");
    id.className = "resource-id";
    id.textContent = item.id;
    wrap.appendChild(id);
    return wrap;
  }

  function addRow(picker, item) {
    const selected = picker.querySelector(".resource-selected");
    const existing = Array.from(selected.querySelectorAll(".resource-row")).find(
      (row) => row.querySelector('input[name="item_id"]').value === item.id
    );
    if (existing) {
      const count = existing.querySelector('input[name="item_count"]');
      count.value = String((parseInt(count.value, 10) || 0) + 1);
      return;
    }
    const row = document.createElement("div");
    row.className = "resource-row";
    const hiddenId = document.createElement("input");
    hiddenId.type = "hidden";
    hiddenId.name = "item_id";
    hiddenId.value = item.id;
    const hiddenType = document.createElement("input");
    hiddenType.type = "hidden";
    hiddenType.name = "item_type";
    hiddenType.value = item.type || "";
    const count = document.createElement("input");
    count.type = "number";
    count.name = "item_count";
    count.min = "1";
    count.max = "999999";
    count.value = "1";
    count.required = true;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-resource";
    remove.textContent = "移除";
    remove.addEventListener("click", () => row.remove());
    row.append(hiddenId, hiddenType, label(item), count, remove);
    selected.appendChild(row);
  }

  function renderResults(picker, items) {
    const box = picker.querySelector(".resource-results");
    box.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "resource-empty";
      empty.textContent = "没有匹配的资源";
      box.appendChild(empty);
      box.classList.add("open");
      return;
    }
    items.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "resource-result";
      button.appendChild(label(item));
      const meta = document.createElement("span");
      meta.className = "resource-type";
      meta.textContent = item.type || "";
      button.appendChild(meta);
      button.addEventListener("click", () => {
        addRow(picker, item);
        box.classList.remove("open");
      });
      box.appendChild(button);
    });
    box.classList.add("open");
  }

  function init(picker) {
    const input = picker.querySelector(".resource-search");
    const box = picker.querySelector(".resource-results");
    let timer = null;
    let seq = 0;
    const search = async () => {
      const current = ++seq;
      try {
        const response = await fetch(`/admin/api/resources?q=${encodeURIComponent(input.value)}`, {
          credentials: "same-origin",
        });
        if (!response.ok) throw new Error("search failed");
        const payload = await response.json();
        if (current === seq) renderResults(picker, payload.items || []);
      } catch (error) {
        if (current === seq) renderResults(picker, []);
      }
    };
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(search, 150);
    });
    input.addEventListener("focus", () => {
      if (!box.children.length) search();
      else box.classList.add("open");
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
      if (event.key === "Escape") box.classList.remove("open");
    });
    document.addEventListener("click", (event) => {
      if (!picker.contains(event.target)) box.classList.remove("open");
    });
    picker.querySelectorAll(".remove-resource").forEach((button) => {
      button.addEventListener("click", () => button.closest(".resource-row").remove());
    });
  }

  document.querySelectorAll(".resource-picker").forEach(init);
})();
