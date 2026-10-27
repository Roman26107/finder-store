document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.control-main input[type="file"]').forEach((input) => {
        const update = () => {
            input.classList.toggle('has-files', Boolean(input.files?.length));
            input.setAttribute(
                'title',
                input.files?.length
                    ? Array.from(input.files).map((file) => file.name).join(', ')
                    : 'Файл не выбран'
            );
        };
        input.addEventListener('change', update);
        update();
    });

    document.querySelectorAll('.control-main .errorlist').forEach((errors) => {
        const control = errors.parentElement?.querySelector('input, select, textarea');
        control?.setAttribute('aria-invalid', 'true');
    });
    const menuButton = document.querySelector('[data-control-menu]');
    const sidebar = document.querySelector('[data-control-sidebar]');
    const overlay = document.querySelector('[data-control-overlay]');
    const toggleMenu = (open) => {
        sidebar?.classList.toggle('open', open);
        overlay?.classList.toggle('open', open);
        menuButton?.setAttribute('aria-expanded', String(open));
        document.body.classList.toggle('control-no-scroll', open);
    };
    menuButton?.addEventListener('click', () => toggleMenu(!sidebar.classList.contains('open')));
    overlay?.addEventListener('click', () => toggleMenu(false));

    document.querySelectorAll('[data-bulk-form]').forEach((form) => {
        const selectAll = form.querySelector('[data-select-all]');
        const rows = Array.from(form.querySelectorAll('[data-row-select]'));
        const counter = form.querySelector('[data-selected-count]');
        const update = () => {
            const count = rows.filter((item) => item.checked).length;
            if (counter) counter.textContent = `Выбрано: ${count}`;
            if (selectAll) {
                selectAll.checked = Boolean(rows.length) && count === rows.length;
                selectAll.indeterminate = count > 0 && count < rows.length;
            }
        };
        selectAll?.addEventListener('change', () => {
            rows.forEach((item) => { item.checked = selectAll.checked; });
            update();
        });
        rows.forEach((item) => item.addEventListener('change', update));
    });

    const initializeDuplicateSearch = (root) => {
        const endpoint = root.dataset.duplicateUrl
            || root.closest('[data-duplicate-url]')?.dataset.duplicateUrl;
        if (!endpoint) return;
        root.querySelectorAll('[data-duplicate-check]').forEach((input) => {
            if (input.dataset.duplicateInitialized) return;
            input.dataset.duplicateInitialized = 'true';
            const kind = input.dataset.duplicateCheck;
            const results = input.parentElement?.querySelector(`[data-duplicate-results="${kind}"]`);
            let timer;
            let requestController;
            const clear = () => {
                results?.replaceChildren();
                if (results) results.hidden = true;
            };
            input.addEventListener('input', () => {
                window.clearTimeout(timer);
                requestController?.abort();
                const query = input.value.trim();
                if (query.length < 2) {
                    clear();
                    return;
                }
                timer = window.setTimeout(async () => {
                    requestController = new AbortController();
                    const params = new URLSearchParams({q: query});
                    const productId = root.closest('[data-product-editor]')?.dataset.productId;
                    if (productId) params.set('exclude', productId);
                    try {
                        const response = await fetch(`${endpoint}?${params}`, {
                            headers: {'X-Requested-With': 'XMLHttpRequest'},
                            signal: requestController.signal,
                        });
                        if (!response.ok) return;
                        const payload = await response.json();
                        clear();
                        if (!results || !payload.results?.length) return;
                        const heading = document.createElement('strong');
                        heading.textContent = 'Похожие товары — проверьте перед созданием';
                        results.append(heading);
                        payload.results.forEach((item) => {
                            const link = document.createElement('a');
                            link.href = item.url;
                            link.target = '_blank';
                            link.rel = 'noopener';
                            const name = document.createElement('b');
                            name.textContent = item.title;
                            const details = document.createElement('small');
                            details.textContent = `${item.catalog} · ${item.brand.toUpperCase()} · ${item.article}`;
                            link.append(name, details);
                            results.append(link);
                        });
                        results.hidden = false;
                    } catch (error) {
                        if (error.name !== 'AbortError') clear();
                    }
                }, 280);
            });
        });
    };

    document.querySelectorAll('[data-duplicate-url]').forEach(initializeDuplicateSearch);

    const collectionBulk = document.querySelector('[data-collection-bulk]');
    if (collectionBulk) {
        const list = collectionBulk.querySelector('[data-draft-list]');
        const template = collectionBulk.querySelector('[data-draft-empty]');
        const totalInput = collectionBulk.querySelector('[name="items-TOTAL_FORMS"]');
        const maxForms = Number(collectionBulk.querySelector('[name="items-MAX_NUM_FORMS"]')?.value || 20);
        const addButton = collectionBulk.querySelector('[data-draft-add]');
        const visibleDrafts = () => Array.from(list.querySelectorAll('[data-draft]')).filter(
            (draft) => !draft.hidden
        );
        const refreshDrafts = () => {
            const drafts = visibleDrafts();
            drafts.forEach((draft, index) => {
                const number = draft.querySelector('[data-draft-number]');
                if (number) number.textContent = String(index + 1).padStart(2, '0');
                const remove = draft.querySelector('[data-draft-remove]');
                if (remove) remove.hidden = drafts.length === 1;
            });
            if (addButton) addButton.disabled = drafts.length >= maxForms;
        };
        const initializeDraft = (draft) => {
            if (draft.dataset.draftInitialized) return;
            draft.dataset.draftInitialized = 'true';
            draft.querySelector('[data-draft-remove]')?.addEventListener('click', () => {
                const deletion = draft.querySelector('input[name$="-DELETE"]');
                if (deletion) deletion.checked = true;
                draft.hidden = true;
                refreshDrafts();
            });
            draft.querySelectorAll('input[type="file"]').forEach((input) => {
                input.addEventListener('change', () => {
                    input.classList.toggle('has-files', Boolean(input.files?.length));
                });
            });
            initializeDuplicateSearch(draft);
        };
        list.querySelectorAll('[data-draft]').forEach(initializeDraft);
        addButton?.addEventListener('click', () => {
            if (!template || !totalInput || visibleDrafts().length >= maxForms) return;
            const index = Number(totalInput.value);
            const holder = document.createElement('div');
            holder.innerHTML = template.innerHTML.replaceAll('__prefix__', String(index)).trim();
            const draft = holder.firstElementChild;
            if (!draft) return;
            list.append(draft);
            totalInput.value = String(index + 1);
            initializeDraft(draft);
            refreshDrafts();
            draft.querySelector('input')?.focus();
        });
        refreshDrafts();
    }

    const editor = document.querySelector('[data-product-editor]');
    if (editor) {
        const colorGroup = editor.querySelector('[name="color_group_reference"]');
        const colorInput = editor.querySelector('[name="current_color"]');
        const colorList = document.querySelector('#color-options');
        if (colorGroup && colorInput && colorList) {
            const allColors = Array.from(colorList.options).map((option) => ({
                value: option.value,
                label: option.label,
                group: option.dataset.colorGroup || '',
            }));
            const renderColorOptions = () => {
                const selectedGroup = colorGroup.value;
                colorList.replaceChildren();
                allColors.filter((color) => !selectedGroup || color.group === selectedGroup).forEach((color) => {
                    const option = document.createElement('option');
                    option.value = color.value;
                    option.label = color.label;
                    option.dataset.colorGroup = color.group;
                    colorList.append(option);
                });
            };
            colorGroup.addEventListener('change', () => {
                colorInput.value = '';
                renderColorOptions();
            });
            renderColorOptions();
        }
        const pricingInputs = Array.from(editor.querySelectorAll('input[name="pricing_mode"]'));
        const fixedPriceWrap = editor.querySelector('[data-fixed-price-wrap]');
        const fixedPrice = editor.querySelector('[data-fixed-price]');
        const updatePricingMode = () => {
            const fixed = pricingInputs.find((input) => input.checked)?.value === 'fixed_rub';
            fixedPriceWrap?.toggleAttribute('hidden', !fixed);
            if (fixedPrice) fixedPrice.required = fixed;
            editor.querySelectorAll('[data-source-price]').forEach((item) => item.classList.toggle('muted-field', fixed));
        };
        pricingInputs.forEach((input) => input.addEventListener('change', updatePricingMode));
        updatePricingMode();

        const materialSelect = editor.querySelector('[data-searchable-multiple="materials"]');
        const materialSearch = editor.querySelector('[data-multiple-search="materials"]');
        const materialChips = editor.querySelector('[data-choice-chips="materials"]');
        if (materialSelect && materialSearch && materialChips) {
            const optionList = document.createElement('div');
            optionList.className = 'multiple-option-list';
            materialSelect.insertAdjacentElement('afterend', optionList);
            const renderMaterials = () => {
                const query = materialSearch.value.trim().toLocaleLowerCase('ru');
                optionList.replaceChildren();
                materialChips.replaceChildren();
                Array.from(materialSelect.options).forEach((option) => {
                    if (option.selected) {
                        const chip = document.createElement('button');
                        chip.type = 'button';
                        chip.textContent = `${option.text} ×`;
                        chip.addEventListener('click', () => { option.selected = false; renderMaterials(); });
                        materialChips.append(chip);
                    }
                    if (!query || option.text.toLocaleLowerCase('ru').includes(query)) {
                        const choice = document.createElement('button');
                        choice.type = 'button';
                        choice.textContent = option.text;
                        choice.classList.toggle('selected', option.selected);
                        choice.addEventListener('click', () => { option.selected = !option.selected; renderMaterials(); });
                        optionList.append(choice);
                    }
                });
            };
            materialSelect.hidden = true;
            materialSearch.addEventListener('input', renderMaterials);
            renderMaterials();
        }

        const profileSelect = editor.querySelector('#id_size_profile');
        const sizeSelect = editor.querySelector('[data-size-values]');
        const sizeButtons = editor.querySelector('[data-size-buttons]');
        const sizeEmpty = editor.querySelector('[data-size-empty]');
        if (profileSelect && sizeSelect && sizeButtons) {
            const renderSizes = () => {
                const profileId = profileSelect.value;
                sizeButtons.replaceChildren();
                let visibleCount = 0;
                Array.from(sizeSelect.options).forEach((option) => {
                    const visible = Boolean(profileId) && option.dataset.profile === profileId;
                    if (!visible && option.selected) option.selected = false;
                    if (!visible) return;
                    visibleCount += 1;
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.textContent = option.text.split(': ').pop();
                    button.classList.toggle('selected', option.selected);
                    button.addEventListener('click', () => { option.selected = !option.selected; button.classList.toggle('selected', option.selected); });
                    sizeButtons.append(button);
                });
                if (sizeEmpty) {
                    sizeEmpty.hidden = visibleCount > 0;
                    sizeEmpty.textContent = profileId ? 'В этом профиле пока нет размеров.' : 'Выберите тип размерного ряда.';
                }
            };
            sizeSelect.hidden = true;
            profileSelect.addEventListener('change', renderSizes);
            renderSizes();
        }
    }

    const bannerEditor = document.querySelector('[data-banner-editor]');
    const initializeBannerPicker = (picker) => {
        if (!bannerEditor || picker.dataset.initialized) return;
        picker.dataset.initialized = 'true';
        const buildImageChoice = (image, includeRemove = false) => {
            const fragment = document.createDocumentFragment();
            const title = document.createElement('b');
            title.textContent = image.title;
            const details = document.createElement('small');
            details.textContent = `${image.article} · фото ${image.position}${image.color ? ` · ${image.color}` : ''}`;
            fragment.append(title, details);
            if (includeRemove) {
                const remove = document.createElement('button');
                remove.type = 'button';
                remove.dataset.clearBannerImage = '';
                remove.textContent = 'Убрать';
                fragment.append(remove);
            }
            return fragment;
        };
        const endpoint = bannerEditor.dataset.imageSearchUrl;
            const row = picker.closest('[data-media-row]');
            const hiddenInput = row?.querySelector('[data-banner-catalog-image]');
            const queryInput = picker.querySelector('[data-banner-image-query]');
            const genderInput = picker.querySelector('[data-banner-image-gender]');
            const searchButton = picker.querySelector('[data-banner-image-search]');
            const results = picker.querySelector('[data-banner-results]');
            const selected = picker.querySelector('[data-banner-selected]');
            const preview = row?.querySelector('[data-media-preview]');
            let timer;

            const choose = (image) => {
                hiddenInput.value = image.id;
                selected.replaceChildren(buildImageChoice(image, true));
                const previewImage = document.createElement('img');
                previewImage.src = image.url;
                previewImage.alt = '';
                preview.replaceChildren(previewImage);
                row.dispatchEvent(new CustomEvent('mediachange', {bubbles: true}));
                results.hidden = true;
            };
            const clear = () => {
                hiddenInput.value = '';
                selected.innerHTML = '<span>Фото из каталога не выбрано</span>';
                if (!row.querySelector('input[type="file"]')?.value) preview.innerHTML = '<span>Новое фото</span>';
                row.dispatchEvent(new CustomEvent('mediachange', {bubbles: true}));
            };
            selected.addEventListener('click', (event) => {
                if (event.target.closest('[data-clear-banner-image]')) clear();
            });
            const search = async () => {
                const params = new URLSearchParams({q: queryInput.value.trim(), gender: genderInput.value});
                results.hidden = false;
                results.innerHTML = '<p>Ищем фотографии…</p>';
                try {
                    const response = await fetch(`${endpoint}?${params}`, {credentials: 'same-origin'});
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const data = await response.json();
                    results.replaceChildren();
                    if (!data.results.length) {
                        results.innerHTML = '<p>Подходящие фотографии не найдены.</p>';
                        return;
                    }
                    data.results.forEach((image) => {
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.className = 'banner-search-result';
                        const thumbnail = document.createElement('img');
                        thumbnail.src = image.url;
                        thumbnail.alt = '';
                        const text = document.createElement('span');
                        text.append(buildImageChoice(image));
                        button.append(thumbnail, text);
                        button.addEventListener('click', () => choose(image));
                        results.append(button);
                    });
                } catch (error) {
                    console.error(error);
                    results.innerHTML = '<p>Не удалось выполнить поиск. Попробуйте ещё раз.</p>';
                }
            };
            searchButton.addEventListener('click', search);
            queryInput.addEventListener('input', () => {
                window.clearTimeout(timer);
                timer = window.setTimeout(search, 350);
            });
            genderInput.addEventListener('change', search);
            queryInput.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') { event.preventDefault(); search(); }
            });
    };

    const genderToggle = bannerEditor?.querySelector('[name="personalize_by_gender"]');
    const updateBannerAudiences = () => {
        const enabled = Boolean(genderToggle?.checked);
        bannerEditor?.classList.toggle('gender-personalization-enabled', enabled);
        bannerEditor?.querySelectorAll('[data-banner-audience]').forEach((field) => {
            field.hidden = !enabled;
            if (!enabled) {
                const all = field.querySelector('input[value="all"]');
                if (all) all.checked = true;
            }
        });
    };
    genderToggle?.addEventListener('change', updateBannerAudiences);

    document.querySelectorAll('[data-media-manager]').forEach((manager) => {
        const list = manager.querySelector('[data-media-list]');
        const template = manager.querySelector('template[data-media-empty]');
        const addButton = manager.querySelector('[data-media-add]');
        const form = manager.closest('form');
        const totalInput = form?.querySelector('[name$="-TOTAL_FORMS"]');
        const limit = Number.parseInt(manager.dataset.mediaLimit, 10) || 10;

        const activeRows = () => Array.from(list.querySelectorAll('[data-media-row]')).filter((row) => {
            const deletion = row.querySelector('input[name$="-DELETE"]');
            return !deletion?.checked;
        });
        const hasPhoto = (row) => Boolean(
            row.dataset.existing === 'true'
            || row.querySelector('input[type="file"]')?.files?.length
            || row.querySelector('[data-banner-catalog-image]')?.value
        );
        const updateRows = () => {
            const rows = activeRows();
            rows.forEach((row, index) => {
                const number = row.querySelector('[data-media-number]');
                const order = row.querySelector('input[name$="-ORDER"]');
                if (number) number.textContent = String(index + 1).padStart(2, '0');
                if (order) order.value = index;
                row.querySelector('[data-media-up]')?.toggleAttribute('disabled', index === 0);
                row.querySelector('[data-media-down]')?.toggleAttribute('disabled', index === rows.length - 1);
            });
            const count = manager.closest('.control-panel')?.querySelector('[data-media-count]');
            if (count) count.textContent = String(rows.filter(hasPhoto).length);
            if (addButton) {
                addButton.disabled = rows.length >= limit;
                addButton.title = rows.length >= limit ? `Достигнут лимит: ${limit}` : '';
            }
        };

        const initializeRow = (row) => {
            if (row.dataset.mediaInitialized) return;
            row.dataset.mediaInitialized = 'true';
            const deletion = row.querySelector('input[name$="-DELETE"]');
            const remove = row.querySelector('[data-media-remove]');
            const restore = row.querySelector('[data-media-restore]');
            const status = row.querySelector('[data-media-status]');
            const preview = row.querySelector('[data-media-preview]');
            const fileInput = row.querySelector('input[type="file"]');

            remove?.addEventListener('click', () => {
                if (row.dataset.existing === 'true') {
                    if (deletion) deletion.checked = true;
                    row.classList.add('is-deleted');
                    remove.hidden = true;
                    restore.hidden = false;
                    if (status) status.textContent = 'Будет удалено после сохранения';
                } else {
                    row.remove();
                }
                updateRows();
            });
            restore?.addEventListener('click', () => {
                if (deletion) deletion.checked = false;
                row.classList.remove('is-deleted');
                remove.hidden = false;
                restore.hidden = true;
                if (status) status.textContent = 'Сохранено';
                updateRows();
            });
            row.querySelector('[data-media-up]')?.addEventListener('click', () => {
                const previous = row.previousElementSibling;
                if (previous) list.insertBefore(row, previous);
                updateRows();
            });
            row.querySelector('[data-media-down]')?.addEventListener('click', () => {
                const next = row.nextElementSibling;
                if (next) list.insertBefore(next, row);
                updateRows();
            });
            fileInput?.addEventListener('change', () => {
                if (fileInput.files?.[0] && preview) {
                    const image = document.createElement('img');
                    image.src = URL.createObjectURL(fileInput.files[0]);
                    image.alt = '';
                    image.addEventListener('load', () => URL.revokeObjectURL(image.src), {once: true});
                    preview.replaceChildren(image);
                    if (status) status.textContent = 'Новое фото выбрано';
                }
                updateRows();
            });
            row.addEventListener('mediachange', updateRows);
            row.querySelectorAll('[data-banner-picker]').forEach(initializeBannerPicker);
        };

        list.querySelectorAll('[data-media-row]').forEach(initializeRow);
        addButton?.addEventListener('click', () => {
            const currentRows = activeRows();
            if (!template || !totalInput || currentRows.length >= limit) return;
            const index = Number.parseInt(totalInput.value, 10);
            const holder = document.createElement('div');
            holder.innerHTML = template.innerHTML.replaceAll('__prefix__', String(index)).trim();
            const row = holder.firstElementChild;
            if (!row) return;
            list.append(row);
            totalInput.value = String(index + 1);
            initializeRow(row);
            updateBannerAudiences();
            updateRows();
            row.querySelector('input[type="file"], input[type="search"]')?.focus();
        });
        updateRows();
    });

    if (bannerEditor) {
        bannerEditor.querySelectorAll('[data-banner-picker]').forEach(initializeBannerPicker);
        updateBannerAudiences();
    }

    document.querySelectorAll('[data-control-messages]').forEach((container) => {
        container.querySelectorAll('[data-control-message]').forEach((message, index) => {
            window.setTimeout(() => {
                if (!message.isConnected) return;
                message.classList.add('leaving');
                const finish = () => {
                    message.remove();
                    if (!container.querySelector('[data-control-message]')) container.remove();
                };
                message.addEventListener('transitionend', finish, {once: true});
                window.setTimeout(finish, 500);
            }, 5000 + (index * 250));
        });
    });
});