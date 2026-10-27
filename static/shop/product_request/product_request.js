document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('[data-request-form]');
    if (!form) return;

    const photo = form.querySelector('[data-request-photo]');
    const photoName = form.querySelector('[data-request-photo-name]');
    photo?.addEventListener('change', () => {
        if (photoName) photoName.textContent = photo.files?.[0]?.name || 'Файл не выбран';
    });

    const items = form.querySelector('[data-request-items]');
    const template = form.querySelector('[data-empty-item]');
    const totalInput = form.querySelector('input[name="items-TOTAL_FORMS"]');
    const addButton = form.querySelector('[data-add-item]');
    if (!items || !template || !totalInput) return;
    const maxForms = Number(
        form.querySelector('input[name="items-MAX_NUM_FORMS"]')?.value || 10
    );

    const visibleItems = () => [...items.querySelectorAll('[data-request-item]')].filter(
        item => !item.hidden
    );

    const refresh = () => {
        const visible = visibleItems();
        visible.forEach((item, index) => {
            const number = item.querySelector('[data-item-number]');
            if (number) number.textContent = String(index + 1).padStart(2, '0');
            const remove = item.querySelector('[data-remove-item]');
            if (remove) remove.hidden = visible.length === 1;
        });
        if (addButton) addButton.disabled = visible.length >= maxForms;
    };

    const bindRemove = (item) => {
        item.querySelector('[data-remove-item]')?.addEventListener('click', () => {
            const deleteInput = item.querySelector('input[name$="-DELETE"]');
            if (deleteInput) deleteInput.checked = true;
            item.hidden = true;
            refresh();
        });
    };

    items.querySelectorAll('[data-request-item]').forEach(bindRemove);
    addButton?.addEventListener('click', () => {
        if (visibleItems().length >= maxForms) return;
        const index = Number(totalInput.value);
        const wrapper = document.createElement('div');
        wrapper.innerHTML = template.innerHTML.replaceAll('__prefix__', String(index)).trim();
        const item = wrapper.firstElementChild;
        items.append(item);
        totalInput.value = String(index + 1);
        bindRemove(item);
        refresh();
        item.querySelector('input')?.focus();
    });

    refresh();
});