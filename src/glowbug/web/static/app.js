document.querySelectorAll("[data-currency-search]").forEach((input) => {
    const selectors = (input.dataset.currencyTarget || "")
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean);

    const applyFilter = () => {
        const query = input.value.trim().toLowerCase();

        selectors.forEach((selector) => {
            document.querySelectorAll(selector).forEach((target) => {
                if (target instanceof HTMLSelectElement) {
                    Array.from(target.options).forEach((option) => {
                        const matches = query === "" || option.textContent.toLowerCase().includes(query);
                        option.hidden = !matches;
                    });
                    return;
                }

                target.querySelectorAll("[data-currency-row]").forEach((row) => {
                    const text = (row.dataset.searchText || row.textContent || "").toLowerCase();
                    row.hidden = query !== "" && !text.includes(query);
                });
            });
        });
    };

    input.addEventListener("input", applyFilter);
});

document.querySelectorAll("[data-add-account]").forEach((button) => {
    button.addEventListener("click", () => {
        const template = document.getElementById("account-row-template");
        const container = document.getElementById("account-rows");
        if (!(template instanceof HTMLTemplateElement) || !container) {
            return;
        }

        const fragment = template.content.cloneNode(true);
        container.appendChild(fragment);

        const rows = container.querySelectorAll(".account-row");
        const lastRow = rows.item(rows.length - 1);
        const firstInput = lastRow?.querySelector('input[name="account_name"]');
        if (firstInput instanceof HTMLInputElement) {
            firstInput.focus();
        }
    });
});

document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
        return;
    }

    const button = target.closest("[data-remove-account]");
    if (!(button instanceof HTMLElement)) {
        return;
    }

    const row = button.closest(".account-row");
    if (row) {
        row.remove();
    }
});
