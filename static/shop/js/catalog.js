document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('[data-catalog-filters]');
    form?.querySelectorAll('[data-auto-filter]').forEach((input) => {
        let wasChecked = false;
        input.addEventListener('pointerdown', () => {
            wasChecked = input.checked;
        });
        input.addEventListener('click', () => {
            if (wasChecked) input.checked = false;
            form.requestSubmit();
            wasChecked = false;
        });
    });

    const setupFilterExpansion = (listSelector, toggleSelector, extraSelector) => {
        const list = form?.querySelector(listSelector);
        const toggle = form?.querySelector(toggleSelector);
        const setExpanded = (expanded) => {
            list?.classList.toggle('expanded', expanded);
            toggle?.setAttribute('aria-expanded', String(expanded));
            toggle?.querySelector('[data-more-text]')?.toggleAttribute('hidden', expanded);
            toggle?.querySelector('[data-less-text]')?.toggleAttribute('hidden', !expanded);
            const icon = toggle?.querySelector('i');
            if (icon) icon.textContent = expanded ? '−' : '+';
        };
        setExpanded(Boolean(list?.querySelector(`${extraSelector} input:checked`)));
        toggle?.addEventListener('click', () => {
            setExpanded(!list.classList.contains('expanded'));
        });
    };

    setupFilterExpansion('[data-color-list]', '[data-color-toggle]', '.color-extra');
    const materialExtra = form?.querySelector('[data-material-extra]');
    const materialToggle = form?.querySelector('[data-material-toggle]');
    const setMaterialsExpanded = (expanded) => {
        if (materialExtra) materialExtra.hidden = !expanded;
        materialToggle?.setAttribute('aria-expanded', String(expanded));
        materialToggle?.querySelector('[data-more-text]')?.toggleAttribute('hidden', expanded);
        materialToggle?.querySelector('[data-less-text]')?.toggleAttribute('hidden', !expanded);
        const icon = materialToggle?.querySelector('i');
        if (icon) icon.textContent = expanded ? '−' : '+';
    };
    setMaterialsExpanded(Boolean(materialExtra?.querySelector('input:checked')));
    materialToggle?.addEventListener('click', () => {
        setMaterialsExpanded(materialExtra.hidden);
    });

    const variantList = document.querySelector('[data-variant-color-list]');
    const variantToggle = document.querySelector('[data-variant-color-toggle]');
    variantToggle?.addEventListener('click', () => {
        const expanded = variantList.classList.toggle('expanded');
        variantToggle.setAttribute('aria-expanded', String(expanded));
        variantToggle.querySelector('[data-more-text]')?.toggleAttribute('hidden', expanded);
        variantToggle.querySelector('[data-less-text]')?.toggleAttribute('hidden', !expanded);
        const icon = variantToggle.querySelector('i');
        if (icon) icon.textContent = expanded ? '−' : '+';
    });

    document.querySelectorAll('[data-size-option]').forEach((input) => {
        input.addEventListener('change', () => {
            const label = document.querySelector('[data-selected-size-label]');
            if (label && input.checked) {
                label.textContent = input.nextElementSibling?.textContent || '';
            }
        });
    });

    const loadMore = document.querySelector('[data-catalog-load-more]');
    loadMore?.addEventListener('click', async (event) => {
        if (!window.fetch || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const grid = document.querySelector('[data-catalog-grid]');
        if (!grid || loadMore.getAttribute('aria-busy') === 'true') return;
        loadMore.setAttribute('aria-busy', 'true');
        const originalText = loadMore.textContent;
        loadMore.textContent = 'Загрузка…';
        try {
            const response = await fetch(loadMore.href, {credentials: 'same-origin'});
            if (!response.ok) throw new Error(`Catalog load failed: ${response.status}`);
            const nextPage = new DOMParser().parseFromString(await response.text(), 'text/html');
            const nextGrid = nextPage.querySelector('[data-catalog-grid]');
            if (!nextGrid) throw new Error('Catalog grid is missing');
            const cards = Array.from(nextGrid.children);
            cards.forEach((card) => grid.append(card));
            document.dispatchEvent(new CustomEvent('catalog:more', {detail: {grid, cards}}));
            const nextLink = nextPage.querySelector('[data-catalog-load-more]');
            if (nextLink) {
                loadMore.href = nextLink.href;
                loadMore.textContent = originalText;
                const currentNote = loadMore.parentElement?.querySelector('small');
                const nextNote = nextLink.parentElement?.querySelector('small');
                if (currentNote && nextNote) currentNote.textContent = nextNote.textContent;
            } else {
                loadMore.parentElement?.remove();
            }
        } catch (error) {
            console.error(error);
            window.location.assign(loadMore.href);
        } finally {
            loadMore.removeAttribute('aria-busy');
        }
    });
});