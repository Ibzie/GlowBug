document.querySelectorAll("[data-transaction-type]").forEach((select) => {
    const form = select.closest("form");
    const destination = form?.querySelector("[data-destination-field]");
    if (!(select instanceof HTMLSelectElement) || !(destination instanceof HTMLElement)) {
        return;
    }

    const update = () => {
        destination.hidden = select.value !== "transfer";
    };

    select.addEventListener("change", update);
    update();
});

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

    const flashClose = target.closest("[data-flash-close]");
    if (flashClose) {
        flashClose.closest(".flash")?.remove();
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

document.querySelectorAll("[data-auto-submit]").forEach((element) => {
    element.addEventListener("change", () => {
        const form = element.closest("form");
        if (form instanceof HTMLFormElement) {
            form.requestSubmit();
        }
    });
});

const scrollContainer = document.querySelector("[data-preserve-scroll]");
if (scrollContainer) {
    const scrollKey = `scroll:${window.location.pathname}${window.location.search}`;
    try {
        const savedScroll = window.sessionStorage.getItem(scrollKey);
        if (savedScroll !== null) {
            window.requestAnimationFrame(() => {
                window.scrollTo(0, Number(savedScroll) || 0);
            });
        }
        window.addEventListener("beforeunload", () => {
            window.sessionStorage.setItem(scrollKey, String(window.scrollY));
        });
    } catch {
        // Session storage can be unavailable in private browsing.
    }
}
