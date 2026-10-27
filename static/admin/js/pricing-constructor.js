document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('[data-pricing-rules]');
    if (!form) return;

    const rows = form.querySelector('[data-rule-rows]');
    const template = form.querySelector('[data-empty-rule]');
    const totalForms = form.querySelector('[name="rules-TOTAL_FORMS"]');

    form.querySelector('[data-add-rule]').addEventListener('click', () => {
        const index = Number(totalForms.value);
        rows.insertAdjacentHTML('beforeend', template.innerHTML.replaceAll('__prefix__', index));
        totalForms.value = index + 1;
    });

    form.addEventListener('click', (event) => {
        const button = event.target.closest('[data-delete-rule]');
        if (!button) return;
        const row = button.closest('.rule-row');
        const deleteInput = row.querySelector('input[name$="-DELETE"]');
        const idInput = row.querySelector('input[name$="-id"]');
        if (idInput && idInput.value) {
            deleteInput.checked = true;
            row.classList.add('is-deleted');
        } else {
            row.remove();
        }
    });
});