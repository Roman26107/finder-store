document.addEventListener('DOMContentLoaded', () => {
    const body = document.body;

    const removeMessage = (message, container) => {
        if (!message.isConnected) return;
        message.classList.add('leaving');
        const finish = () => {
            message.remove();
            if (container && !container.querySelector('[data-message]')) container.remove();
        };
        message.addEventListener('transitionend', finish, {once: true});
        window.setTimeout(finish, 500);
    };
    document.querySelectorAll('[data-messages]').forEach((container) => {
        container.querySelectorAll('[data-message]').forEach((message, index) => {
            window.setTimeout(() => removeMessage(message, container), 5000 + (index * 250));
        });
    });

    const formatRussianPhone = (value) => {
        const raw = value.trim();
        const digits = raw.replace(/\D/g, '');
        let national = null;
        if (raw.startsWith('+7')) {
            if (digits.length > 11) return value;
            national = digits.slice(1);
        }
        else if (!raw.startsWith('+') && (digits.startsWith('7') || digits.startsWith('8'))) {
            if (digits.length > 11) return value;
            national = digits.slice(1);
        } else if (!raw.startsWith('+') && digits.length <= 10) national = digits;
        if (national === null) return value;
        let formatted = '+7';
        if (national.length) formatted += ` ${national.slice(0, 3)}`;
        if (national.length > 3) formatted += ` ${national.slice(3, 6)}`;
        if (national.length > 6) formatted += `-${national.slice(6, 8)}`;
        if (national.length > 8) formatted += `-${national.slice(8, 10)}`;
        return formatted;
    };
    document.querySelectorAll('[data-phone-input]').forEach((input) => {
        const format = () => {
            const formatted = formatRussianPhone(input.value);
            if (formatted !== input.value) input.value = formatted;
        };
        input.addEventListener('input', format);
        input.addEventListener('blur', format);
        if (input.value) format();
    });

    document.querySelectorAll('.form-field, .request-field, .checkout-field').forEach((field) => {
        const control = field.querySelector('input:not([type="hidden"]), select, textarea');
        if (!control) return;
        const updateFilled = () => field.classList.toggle('is-filled', Boolean(control.value));
        updateFilled();
        control.addEventListener('input', updateFilled);
        control.addEventListener('change', updateFilled);
        if (field.classList.contains('has-error')) control.setAttribute('aria-invalid', 'true');
    });

    document.querySelectorAll('.form-field input[type="password"]').forEach((input) => {
        if (input.closest('.password-control')) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'password-control';
        input.parentNode.insertBefore(wrapper, input);
        wrapper.append(input);
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'password-toggle';
        toggle.textContent = 'Показать';
        toggle.setAttribute('aria-label', 'Показать пароль');
        toggle.setAttribute('aria-pressed', 'false');
        toggle.addEventListener('click', () => {
            const visible = input.type === 'text';
            input.type = visible ? 'password' : 'text';
            toggle.textContent = visible ? 'Показать' : 'Скрыть';
            toggle.setAttribute('aria-label', visible ? 'Показать пароль' : 'Скрыть пароль');
            toggle.setAttribute('aria-pressed', String(!visible));
            input.focus({preventScroll: true});
        });
        wrapper.append(toggle);
    });

    const menuButton = document.querySelector('[data-menu-button]');
    const mainNav = document.querySelector('[data-main-nav]');
    const megaMenu = document.querySelector('[data-mega-menu]');
    const megaTriggers = Array.from(document.querySelectorAll('[data-mega-trigger]'));
    const megaPanels = Array.from(document.querySelectorAll('[data-mega-panel]'));
    let megaCloseTimer = null;

    const closeMobileMenu = () => {
        if (!mainNav || !menuButton) return;
        mainNav.classList.remove('open');
        menuButton.setAttribute('aria-expanded', 'false');
        menuButton.setAttribute('aria-label', 'Открыть меню');
        body.classList.remove('no-scroll');
    };

    const closeMegaMenu = () => {
        window.clearTimeout(megaCloseTimer);
        if (megaMenu) megaMenu.hidden = true;
        megaPanels.forEach((panel) => { panel.hidden = true; });
        megaTriggers.forEach((trigger) => trigger.setAttribute('aria-expanded', 'false'));
    };
    const openMegaMenu = (name) => {
        if (!megaMenu || window.matchMedia('(max-width: 980px)').matches) return;
        window.clearTimeout(megaCloseTimer);
        let hasPanel = false;
        megaPanels.forEach((panel) => {
            const active = panel.dataset.megaPanel === name;
            panel.hidden = !active;
            hasPanel = hasPanel || active;
        });
        megaTriggers.forEach((trigger) => {
            trigger.setAttribute('aria-expanded', String(trigger.dataset.megaTrigger === name));
        });
        megaMenu.hidden = !hasPanel;
    };
    megaTriggers.forEach((trigger) => {
        trigger.setAttribute('aria-haspopup', 'true');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.addEventListener('mouseenter', () => openMegaMenu(trigger.dataset.megaTrigger));
        trigger.addEventListener('focus', () => openMegaMenu(trigger.dataset.megaTrigger));
    });
    mainNav?.querySelectorAll('a:not([data-mega-trigger])').forEach((link) => {
        link.addEventListener('mouseenter', closeMegaMenu);
        link.addEventListener('focus', closeMegaMenu);
    });
    document.querySelector('.header-inner')?.addEventListener('mouseleave', () => {
        megaCloseTimer = window.setTimeout(closeMegaMenu, 140);
    });
    megaMenu?.addEventListener('mouseenter', () => window.clearTimeout(megaCloseTimer));
    megaMenu?.addEventListener('mouseleave', () => {
        megaCloseTimer = window.setTimeout(closeMegaMenu, 140);
    });
    menuButton?.addEventListener('click', () => {
        closeMegaMenu();
        const open = mainNav.classList.toggle('open');
        menuButton.setAttribute('aria-expanded', String(open));
        menuButton.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
        body.classList.toggle('no-scroll', open);
    });
    mainNav?.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeMobileMenu));

    const thingsPanel = document.querySelector('[data-things-panel]');
    const thingsScrim = document.querySelector('[data-things-scrim]');
    const thingsBody = thingsPanel?.querySelector('[data-things-body]');
    const thingsTabs = Array.from(thingsPanel?.querySelectorAll('[data-things-tab]') || []);
    let thingsActiveTab = 'cart';
    let thingsReturnFocus = null;

    const showThingsTab = (name) => {
        thingsActiveTab = name === 'favorites' ? 'favorites' : 'cart';
        thingsTabs.forEach((tab) => {
            tab.setAttribute('aria-selected', String(tab.dataset.thingsTab === thingsActiveTab));
        });
        thingsBody?.querySelectorAll('[data-things-content]').forEach((content) => {
            const active = content.dataset.thingsContent === thingsActiveTab;
            content.hidden = !active;
            content.classList.toggle('active', active);
        });
    };
    const loadThings = async () => {
        if (!thingsPanel || !thingsBody) return;
        thingsBody.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(thingsPanel.dataset.endpoint, {
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            if (!response.ok) throw new Error(`Things request failed: ${response.status}`);
            thingsBody.innerHTML = await response.text();
            showThingsTab(thingsActiveTab);
        } catch (error) {
            console.error(error);
            thingsBody.innerHTML = '<div class="things-empty"><h3>Не удалось загрузить</h3><p>Откройте корзину отдельной страницей.</p><a class="button button-dark" href="/cart/">Перейти в корзину</a></div>';
        } finally {
            thingsBody.removeAttribute('aria-busy');
        }
    };
    const closeThings = (returnFocus = true) => {
        if (!thingsPanel || !thingsScrim) return;
        thingsPanel.classList.remove('open');
        thingsPanel.setAttribute('aria-hidden', 'true');
        thingsScrim.classList.remove('open');
        body.classList.remove('no-scroll');
        window.setTimeout(() => { thingsScrim.hidden = true; }, 260);
        if (returnFocus) thingsReturnFocus?.focus?.({preventScroll: true});
    };
    const openThings = async (tab = 'cart', trigger = null) => {
        if (!thingsPanel || !thingsScrim) return;
        thingsReturnFocus = trigger || document.activeElement;
        thingsActiveTab = tab === 'favorites' ? 'favorites' : 'cart';
        thingsScrim.hidden = false;
        window.requestAnimationFrame(() => thingsScrim.classList.add('open'));
        thingsPanel.classList.add('open');
        thingsPanel.setAttribute('aria-hidden', 'false');
        body.classList.add('no-scroll');
        await loadThings();
        thingsPanel.querySelector('[data-things-close]')?.focus({preventScroll: true});
    };
    document.querySelectorAll('[data-things-open]').forEach((trigger) => {
        trigger.addEventListener('click', (event) => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            closeMobileMenu();
            openThings(trigger.dataset.thingsOpen, trigger);
        });
    });
    thingsTabs.forEach((tab) => tab.addEventListener('click', () => showThingsTab(tab.dataset.thingsTab)));
    thingsPanel?.querySelector('[data-things-close]')?.addEventListener('click', () => closeThings());
    thingsScrim?.addEventListener('click', () => closeThings());
    thingsBody?.addEventListener('submit', async (event) => {
        const form = event.target.closest('[data-things-action]');
        if (!form || !window.fetch) return;
        event.preventDefault();
        const submitter = event.submitter;
        const data = new FormData(form);
        if (submitter?.name) data.set(submitter.name, submitter.value);
        try {
            const isFavorite = form.matches('[data-favorite-form]');
            const response = await fetch(form.action, {
                method: 'POST', body: data, credentials: 'same-origin',
                headers: {'X-Requested-With': isFavorite ? 'XMLHttpRequest' : 'ThingsPanel'},
            });
            if (!response.ok) throw new Error(`Things action failed: ${response.status}`);
            if (isFavorite) {
                const result = await response.json();
                setFavoriteState(result.product_id, result.is_favorite);
                setFavoriteCount(result.favorite_count);
            }
            await loadThings();
        } catch (error) {
            console.error(error);
            HTMLFormElement.prototype.submit.call(form);
        }
    });

    const accountMenu = document.querySelector('[data-account-menu]');
    const accountButton = accountMenu?.querySelector('[data-account-button]');
    const accountPopover = accountMenu?.querySelector('[data-account-popover]');
    const closeAccountMenu = (returnFocus = false) => {
        if (!accountButton || !accountPopover) return;
        accountPopover.hidden = true;
        accountButton.setAttribute('aria-expanded', 'false');
        accountMenu.classList.remove('open');
        if (returnFocus) accountButton.focus();
    };
    const openAccountMenu = () => {
        if (!accountButton || !accountPopover || window.matchMedia('(max-width: 980px)').matches) return;
        accountPopover.hidden = false;
        accountButton.setAttribute('aria-expanded', 'true');
        accountMenu.classList.add('open');
    };
    accountButton?.addEventListener('click', () => {
        if (window.matchMedia('(max-width: 980px)').matches) {
            window.location.assign(accountButton.dataset.accountUrl);
            return;
        }
        if (accountPopover.hidden) openAccountMenu();
        else closeAccountMenu();
    });
    document.addEventListener('click', (event) => {
        if (accountMenu && !accountMenu.contains(event.target)) closeAccountMenu();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (thingsPanel?.classList.contains('open')) {
            closeThings();
            return;
        }
        closeMegaMenu();
        closeMobileMenu();
        if (accountMenu?.classList.contains('open')) closeAccountMenu(true);
        toggleSearch(false);
    });
    window.addEventListener('resize', () => {
        if (window.matchMedia('(min-width: 981px)').matches) closeMobileMenu();
        else closeAccountMenu();
    });

    const searchPanel = document.querySelector('[data-search-panel]');
    const toggleSearch = (show) => {
        if (!searchPanel) return;
        searchPanel.hidden = !show;
        if (show) searchPanel.querySelector('input')?.focus();
    };
    document.querySelectorAll('[data-search-button]').forEach((button) => {
        button.addEventListener('click', () => {
            if (mainNav?.classList.contains('open')) {
                closeMobileMenu();
            }
            closeAccountMenu();
            toggleSearch(true);
        });
    });
    document.querySelector('[data-search-close]')?.addEventListener('click', () => toggleSearch(false));

    const closeDialog = (dialog) => {
        if (dialog?.open) dialog.close();
    };
    document.querySelectorAll('[data-dialog-close]').forEach((button) => {
        button.addEventListener('click', () => closeDialog(button.closest('dialog')));
    });
    document.querySelectorAll('.finder-dialog').forEach((dialog) => {
        dialog.addEventListener('click', (event) => {
            if (event.target === dialog) closeDialog(dialog);
        });
    });
    const sizeGuideDialog = document.querySelector('[data-size-guide-dialog]');
    document.querySelector('[data-size-guide-open]')?.addEventListener('click', () => {
        sizeGuideDialog?.showModal();
    });

    document.querySelectorAll('[data-weekly-look]').forEach((look) => {
        const controls = Array.from(look.querySelectorAll('[data-look-index]'));
        const itemButtons = Array.from(look.querySelectorAll('.weekly-look-info [data-look-index]'));
        const popover = look.querySelector('[data-look-popover]');
        const show = (index) => {
            const item = itemButtons[index];
            if (!item || !popover) return;
            controls.forEach((control) => {
                const active = Number(control.dataset.lookIndex) === index;
                control.setAttribute('aria-pressed', String(active));
                control.setAttribute('aria-current', String(active));
            });
            popover.querySelector('small').textContent = item.dataset.brand || '';
            popover.querySelector('b').textContent = item.dataset.title || '';
            popover.querySelector('strong').textContent = item.dataset.price || '';
            popover.querySelector('a').href = item.dataset.url || '#';
        };
        controls.forEach((control) => {
            control.addEventListener('click', () => show(Number(control.dataset.lookIndex)));
        });
        show(0);
    });

    document.querySelectorAll('[data-product-carousel]').forEach((carousel) => {
        const track = carousel.querySelector('[data-product-carousel-track]');
        const previous = carousel.querySelector('[data-product-carousel-prev]');
        const next = carousel.querySelector('[data-product-carousel-next]');
        if (!track) return;

        const scrollDistance = () => {
            const card = track.querySelector('.product-card');
            const gap = Number.parseFloat(window.getComputedStyle(track).columnGap) || 0;
            return card ? card.getBoundingClientRect().width + gap : track.clientWidth * 0.8;
        };
        const updateButtons = () => {
            const maxScroll = Math.max(0, track.scrollWidth - track.clientWidth);
            previous?.toggleAttribute('disabled', track.scrollLeft <= 2);
            next?.toggleAttribute('disabled', track.scrollLeft >= maxScroll - 2);
        };
        previous?.addEventListener('click', () => {
            track.scrollBy({left: -scrollDistance(), behavior: 'smooth'});
        });
        next?.addEventListener('click', () => {
            track.scrollBy({left: scrollDistance(), behavior: 'smooth'});
        });
        track.addEventListener('scroll', updateButtons, {passive: true});
        window.addEventListener('resize', updateButtons);
        updateButtons();
    });

    const mobileGallery = window.matchMedia('(max-width: 760px)');
    const enableCyclicSwipe = (surface, getIndex, setIndex, onSwipe = () => {}) => {
        let gesture = null;
        const directionThreshold = 3;

        const finish = (event, cancelled = false) => {
            if (!gesture || (event.pointerId !== undefined && gesture.pointerId !== event.pointerId)) return;
            const completed = gesture;
            const endX = event.clientX ?? completed.lastX;
            const endY = event.clientY ?? completed.lastY;
            const deltaX = endX - completed.startX;
            const deltaY = endY - completed.startY;
            const minDistance = Math.max(10, Math.min(20, surface.clientWidth * 0.04));
            gesture = null;
            if (surface.hasPointerCapture?.(completed.pointerId)) {
                surface.releasePointerCapture(completed.pointerId);
            }
            surface.classList.remove('is-dragging');
            if (
                cancelled
                || !completed.horizontal
                || Math.abs(deltaX) < minDistance
                || Math.abs(deltaX) <= Math.abs(deltaY) * 0.55
            ) return;
            onSwipe();
            setIndex(getIndex() + (deltaX < 0 ? 1 : -1), true);
        };

        surface.addEventListener('pointerdown', (event) => {
            if (!mobileGallery.matches || event.button > 0) return;
            gesture = {
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                lastX: event.clientX,
                lastY: event.clientY,
                horizontal: false,
                vertical: false,
            };
            surface.setPointerCapture?.(event.pointerId);
        });
        surface.addEventListener('pointermove', (event) => {
            if (!gesture || gesture.pointerId !== event.pointerId) return;
            const deltaX = event.clientX - gesture.startX;
            const deltaY = event.clientY - gesture.startY;
            gesture.lastX = event.clientX;
            gesture.lastY = event.clientY;
            if (!gesture.horizontal && !gesture.vertical && (
                Math.abs(deltaX) > directionThreshold || Math.abs(deltaY) > directionThreshold
            )) {
                gesture.horizontal = Math.abs(deltaX) > Math.abs(deltaY) * 0.58;
                gesture.vertical = !gesture.horizontal;
                if (gesture.horizontal) surface.classList.add('is-dragging');
            }
            if (gesture.horizontal) event.preventDefault();
        });
        surface.addEventListener('pointerup', (event) => finish(event));
        surface.addEventListener('pointercancel', (event) => finish(event, true));
        surface.addEventListener('lostpointercapture', (event) => finish(event));
    };

    document.querySelectorAll('[data-hero-slider]').forEach((slider) => {
        const slides = Array.from(slider.querySelectorAll('[data-hero-slide]'));
        const dots = Array.from(slider.querySelectorAll('[data-hero-dot]'));
        if (slides.length < 2) return;

        const interval = Number.parseInt(slider.dataset.heroInterval, 10) || 5000;
        const autoplay = slider.dataset.heroAutoplay !== 'false';
        let currentIndex = Math.max(0, slides.findIndex((slide) => slide.classList.contains('active')));
        let timerId = null;

        const showSlide = (index) => {
            currentIndex = (index + slides.length) % slides.length;
            slides.forEach((slide, slideIndex) => {
                const active = slideIndex === currentIndex;
                slide.classList.toggle('active', active);
                slide.toggleAttribute('aria-hidden', !active);
            });
            dots.forEach((dot, dotIndex) => {
                const active = dotIndex === currentIndex;
                dot.classList.toggle('active', active);
                dot.toggleAttribute('aria-current', active);
            });
        };
        const stopAutoplay = () => {
            if (timerId !== null) window.clearInterval(timerId);
            timerId = null;
        };
        const startAutoplay = () => {
            stopAutoplay();
            if (autoplay && !document.hidden) {
                timerId = window.setInterval(() => showSlide(currentIndex + 1), interval);
            }
        };

        dots.forEach((dot, index) => {
            dot.addEventListener('click', () => {
                showSlide(index);
                startAutoplay();
            });
        });
        slider.querySelector('[data-hero-prev]')?.addEventListener('click', () => {
            showSlide(currentIndex - 1);
            startAutoplay();
        });
        slider.querySelector('[data-hero-next]')?.addEventListener('click', () => {
            showSlide(currentIndex + 1);
            startAutoplay();
        });
        enableCyclicSwipe(slider, () => currentIndex, (index) => {
            showSlide(index);
            startAutoplay();
        }, stopAutoplay);
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) stopAutoplay();
            else startAutoplay();
        });
        startAutoplay();
    });

    const filters = document.querySelector('[data-filters]');
    const toggleFilters = (show) => {
        filters?.classList.toggle('open', show);
        body.classList.toggle('no-scroll', show);
    };
    document.querySelector('[data-filter-button]')?.addEventListener('click', () => toggleFilters(true));
    document.querySelector('[data-filter-close]')?.addEventListener('click', () => toggleFilters(false));

    document.querySelector('[data-sort-button]')?.addEventListener('click', () => {
        document.querySelector('[data-sort-select]')?.focus();
        document.querySelector('.catalog-toolbar')?.scrollIntoView({behavior: 'smooth'});
    });
    document.querySelector('[data-sort-select]')?.addEventListener('change', (event) => {
        event.currentTarget.form.submit();
    });

    const nearestSlideIndex = (track, slides) => {
        const left = track.scrollLeft;
        return slides.reduce((nearest, slide, index) => (
            Math.abs(slide.offsetLeft - left) < Math.abs(slides[nearest].offsetLeft - left)
                ? index : nearest
        ), 0);
    };
    document.querySelectorAll('[data-detail-gallery]').forEach((gallery) => {
        const track = gallery.querySelector('[data-detail-gallery-track]');
        const slides = Array.from(gallery.querySelectorAll('[data-gallery-slide]'));
        const thumbs = Array.from(gallery.querySelectorAll('[data-gallery-thumb]'));
        const dots = Array.from(gallery.querySelectorAll('[data-detail-gallery-dot]'));
        const previous = gallery.querySelector('[data-detail-gallery-prev]');
        const next = gallery.querySelector('[data-detail-gallery-next]');
        if (!track || !slides.length) return;
        let currentIndex = Math.max(0, slides.findIndex((slide) => slide.classList.contains('active')));
        let scrollFrame = null;

        const setDetailIndex = (index, scroll = false, behavior = 'smooth') => {
            currentIndex = Math.max(0, Math.min(index, slides.length - 1));
            slides.forEach((slide, slideIndex) => {
                const active = slideIndex === currentIndex;
                slide.classList.toggle('active', active);
                slide.toggleAttribute('aria-hidden', !active && !mobileGallery.matches);
            });
            thumbs.forEach((thumb, thumbIndex) => {
                const active = thumbIndex === currentIndex;
                thumb.classList.toggle('active', active);
                thumb.toggleAttribute('aria-current', active);
            });
            dots.forEach((dot, dotIndex) => {
                const active = dotIndex === currentIndex;
                dot.classList.toggle('active', active);
                dot.toggleAttribute('aria-current', active);
            });
            previous?.toggleAttribute('disabled', currentIndex === 0);
            next?.toggleAttribute('disabled', currentIndex === slides.length - 1);
            if (scroll && mobileGallery.matches) {
                track.scrollTo({left: slides[currentIndex].offsetLeft, behavior});
            }
            thumbs[currentIndex]?.scrollIntoView({block: 'nearest', inline: 'nearest'});
        };
        thumbs.forEach((thumb, index) => thumb.addEventListener('click', () => setDetailIndex(index, true)));
        dots.forEach((dot, index) => dot.addEventListener('click', () => setDetailIndex(index, true)));
        previous?.addEventListener('click', () => setDetailIndex(currentIndex - 1));
        next?.addEventListener('click', () => setDetailIndex(currentIndex + 1));
        track.addEventListener('scroll', () => {
            if (!mobileGallery.matches || scrollFrame !== null) return;
            scrollFrame = window.requestAnimationFrame(() => {
                scrollFrame = null;
                setDetailIndex(nearestSlideIndex(track, slides));
            });
        }, {passive: true});
        setDetailIndex(currentIndex);
        if (mobileGallery.matches) {
            window.requestAnimationFrame(() => setDetailIndex(currentIndex, true, 'auto'));
        }
    });

    document.querySelectorAll('.quantity-form').forEach((form) => {
        const input = form.querySelector('input[name="quantity"]');
        const submit = () => form.requestSubmit();
        form.querySelector('[data-quantity-minus]')?.addEventListener('click', () => {
            input.value = Math.max(0, Number(input.value) - 1);
            submit();
        });
        form.querySelector('[data-quantity-plus]')?.addEventListener('click', () => {
            input.value = Math.min(99, Number(input.value) + 1);
            submit();
        });
        input?.addEventListener('change', submit);
    });

    const deliveryAddress = document.querySelector('#id_delivery_address');
    document.querySelectorAll('[data-address-choice]').forEach((choice) => {
        choice.addEventListener('change', () => {
            if (choice.checked && deliveryAddress) {
                deliveryAddress.value = choice.dataset.addressValue || '';
                deliveryAddress.dispatchEvent(new Event('input', {bubbles: true}));
            }
        });
        if (choice.checked && choice.dataset.addressValue && deliveryAddress && !deliveryAddress.value) {
            deliveryAddress.value = choice.dataset.addressValue;
        }
    });

    const checkoutSummary = document.querySelector('.checkout-summary[data-cart-total]');
    const promoInput = document.querySelector('#id_promo_code');
    const formatCheckoutPrice = (value) => new Intl.NumberFormat('ru-RU', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2,
    }).format(value);
    const updateCheckoutTotal = () => {
        if (!checkoutSummary) return;
        const subtotal = Number.parseFloat(checkoutSummary.dataset.cartTotal || '0');
        const delivery = document.querySelector('input[name="delivery_method"]:checked')?.value || '';
        const deliveryCost = Number.parseFloat(checkoutSummary.dataset[`delivery${delivery.charAt(0).toUpperCase()}${delivery.slice(1)}`] || '0');
        const discount = promoInput?.value.trim().toUpperCase() === 'WELCOME10'
            ? Math.round(subtotal * 10) / 100 : 0;
        const row = document.querySelector('[data-discount-row]');
        if (row) row.hidden = !discount;
        const discountValue = document.querySelector('[data-discount-value]');
        const deliveryValue = document.querySelector('[data-delivery-value]');
        const totalValue = document.querySelector('[data-checkout-total]');
        if (discountValue) discountValue.textContent = formatCheckoutPrice(discount);
        if (deliveryValue) deliveryValue.textContent = formatCheckoutPrice(deliveryCost);
        if (totalValue) totalValue.textContent = formatCheckoutPrice(subtotal - discount + deliveryCost);
    };
    document.querySelectorAll('input[name="delivery_method"]').forEach((input) => {
        input.addEventListener('change', updateCheckoutTotal);
    });
    promoInput?.addEventListener('input', updateCheckoutTotal);
    document.querySelector('[data-promo-apply]')?.addEventListener('click', () => {
        const valid = promoInput?.value.trim().toUpperCase() === 'WELCOME10';
        const message = document.querySelector('[data-promo-message]');
        updateCheckoutTotal();
        if (message) {
            message.textContent = valid ? 'Промокод применён' : (promoInput?.value.trim() ? 'Промокод не найден' : '');
            message.classList.toggle('error', !valid && Boolean(promoInput?.value.trim()));
        }
    });

    const checkoutSteps = document.querySelector('[data-checkout-steps]');
    if (checkoutSteps) {
        checkoutSteps.classList.add('js-checkout-steps');
        const sections = Array.from(checkoutSteps.querySelectorAll('[data-checkout-step]'));
        const indicators = Array.from(document.querySelectorAll('[data-checkout-indicator]'));
        let step = Math.max(1, sections.findIndex((section) => section.querySelector('.has-error')) + 1);
        const renderStep = () => {
            sections.forEach((section) => section.classList.toggle(
                'active', Number(section.dataset.checkoutStep) === step,
            ));
            indicators.forEach((indicator) => {
                const number = Number(indicator.dataset.checkoutIndicator);
                indicator.classList.toggle('active', number === step);
                indicator.classList.toggle('done', number < step);
            });
            sections[step - 1]?.scrollIntoView({behavior: 'smooth', block: 'start'});
        };
        checkoutSteps.querySelectorAll('[data-checkout-next]').forEach((button) => {
            button.addEventListener('click', () => {
                if (step === 2 && deliveryAddress && !deliveryAddress.checkValidity()) {
                    deliveryAddress.reportValidity();
                    return;
                }
                step = Math.min(3, step + 1);
                renderStep();
            });
        });
        checkoutSteps.querySelectorAll('[data-checkout-back]').forEach((button) => {
            button.addEventListener('click', () => {
                step = Math.max(1, step - 1);
                renderStep();
            });
        });
        renderStep();
    }

    const setFavoriteState = (productId, isFavorite) => {
        document.querySelectorAll(`[data-favorite-form][data-product-id="${productId}"]`).forEach((form) => {
            const button = form.querySelector('[data-favorite-button]');
            if (!button) return;
            button.classList.toggle('active', isFavorite);
            button.setAttribute('aria-pressed', String(isFavorite));
            button.setAttribute('aria-label', isFavorite ? 'Удалить из избранного' : 'Добавить в избранное');
            const label = button.querySelector('[data-favorite-label]');
            if (label) label.textContent = isFavorite
                ? (button.dataset.favoriteLabelRemove || 'Удалить из избранного')
                : (button.dataset.favoriteLabelAdd || 'Добавить в избранное');
        });
    };

    const setFavoriteCount = (count) => {
        const tab = document.querySelector('[data-things-tab="favorites"]');
        if (!tab) return;
        let badge = tab.querySelector('span');
        if (!badge && count > 0) {
            badge = document.createElement('span');
            tab.append(' ', badge);
        }
        if (badge) {
            badge.textContent = String(count);
            badge.hidden = count === 0;
        }
    };

    const bindFavoriteForms = (root = document) => root.querySelectorAll('[data-favorite-form]').forEach((form) => {
        form.addEventListener('submit', async (event) => {
            if (!window.fetch) return;
            event.preventDefault();
            const button = form.querySelector('[data-favorite-button]');
            if (button?.disabled) return;
            if (button) button.disabled = true;
            try {
                const response = await fetch(form.action, {
                    method: 'POST',
                    body: new FormData(form),
                    credentials: 'same-origin',
                    headers: {'X-Requested-With': 'XMLHttpRequest'},
                });
                if (!response.ok) throw new Error(`Favorite request failed: ${response.status}`);
                const result = await response.json();
                setFavoriteState(result.product_id, result.is_favorite);
                setFavoriteCount(result.favorite_count);
                if (!result.is_favorite && document.querySelector('[data-favorites-page]')) {
                    const card = form.closest('.product-card');
                    card?.remove();
                    const heading = document.querySelector('.favorites-heading h1');
                    if (heading) heading.textContent = result.favorite_count ? `Избранное (${result.favorite_count})` : 'Избранное';
                    if (!result.favorite_count) window.location.reload();
                }
            } catch (error) {
                console.error(error);
                HTMLFormElement.prototype.submit.call(form);
            } finally {
                if (button) button.disabled = false;
            }
        });
    });
    bindFavoriteForms();

    const bindCardGalleries = (root = document) => root.querySelectorAll('[data-card-gallery]').forEach((gallery) => {
        const track = gallery.querySelector('[data-card-gallery-track]');
        const slides = Array.from(gallery.querySelectorAll('[data-card-gallery-slide]'));
        const dots = Array.from(gallery.querySelectorAll('[data-card-gallery-dot]'));
        const previous = gallery.querySelector('[data-card-gallery-prev]');
        const next = gallery.querySelector('[data-card-gallery-next]');
        if (!track || !slides.length) return;
        let currentIndex = Math.max(0, slides.findIndex((slide) => slide.classList.contains('active')));
        let scrollFrame = null;
        let suppressClick = false;
        let clickBlockTimer = null;
        let swipeGesture = null;

        const blockCardClick = (timeout = 420) => {
            suppressClick = true;
            window.clearTimeout(clickBlockTimer);
            clickBlockTimer = window.setTimeout(() => { suppressClick = false; }, timeout);
        };

        const showImage = (index, scroll = false, behavior = 'smooth') => {
            currentIndex = Math.max(0, Math.min(index, slides.length - 1));
            slides.forEach((slide, slideIndex) => {
                const active = slideIndex === currentIndex;
                slide.classList.toggle('active', active);
                slide.toggleAttribute('aria-hidden', !active && !mobileGallery.matches);
            });
            dots.forEach((dot, dotIndex) => {
                const active = dotIndex === currentIndex;
                dot.classList.toggle('active', active);
                dot.toggleAttribute('aria-current', active);
            });
            previous?.toggleAttribute('disabled', currentIndex === 0);
            next?.toggleAttribute('disabled', currentIndex === slides.length - 1);
            if (scroll && mobileGallery.matches) {
                track.scrollTo({left: slides[currentIndex].offsetLeft, behavior});
            }
        };
        previous?.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            showImage(currentIndex - 1);
        });
        next?.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            showImage(currentIndex + 1);
        });
        dots.forEach((dot, index) => {
            dot.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                showImage(index, true);
            });
        });
        track.addEventListener('pointerdown', (event) => {
            if (!mobileGallery.matches || event.button > 0) return;
            swipeGesture = {pointerId: event.pointerId, startX: event.clientX, startY: event.clientY};
        });
        track.addEventListener('pointermove', (event) => {
            if (!swipeGesture || swipeGesture.pointerId !== event.pointerId) return;
            const deltaX = event.clientX - swipeGesture.startX;
            const deltaY = event.clientY - swipeGesture.startY;
            if (Math.abs(deltaX) > 8 && Math.abs(deltaX) > Math.abs(deltaY)) blockCardClick();
        }, {passive: true});
        const clearSwipeGesture = (event) => {
            if (swipeGesture && swipeGesture.pointerId === event.pointerId) swipeGesture = null;
        };
        track.addEventListener('pointerup', clearSwipeGesture);
        track.addEventListener('pointercancel', clearSwipeGesture);
        track.addEventListener('scroll', () => {
            if (!mobileGallery.matches || scrollFrame !== null) return;
            blockCardClick(220);
            scrollFrame = window.requestAnimationFrame(() => {
                scrollFrame = null;
                showImage(nearestSlideIndex(track, slides));
            });
        }, {passive: true});
        gallery.querySelector('.product-image-link')?.addEventListener('click', (event) => {
            if (!suppressClick) return;
            event.preventDefault();
            event.stopPropagation();
            suppressClick = false;
            window.clearTimeout(clickBlockTimer);
        }, true);
        showImage(currentIndex);
        if (mobileGallery.matches) {
            window.requestAnimationFrame(() => showImage(currentIndex, true, 'auto'));
        }
    });
    bindCardGalleries();
    document.addEventListener('catalog:more', (event) => {
        event.detail.cards.forEach((card) => {
            bindFavoriteForms(card);
            bindCardGalleries(card);
        });
    });
});