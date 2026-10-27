document.addEventListener('DOMContentLoaded', () => {
    const endpoint = document.body.dataset.analyticsEndpoint;
    if (!endpoint || navigator.webdriver) return;
    const csrf = document.body.dataset.analyticsCsrf;
    const startedAt = Date.now();
    const sentScroll = new Set();
    const productRoot = document.querySelector('[data-analytics-product]');

    const productData = (element = productRoot) => element ? {
        product_id: Number(element.dataset.productId) || null,
        product_sku: element.dataset.productSku || '',
        brand: element.dataset.productBrand || '',
        category: element.dataset.productCategory || '',
    } : {};

    const send = (type, details = {}) => {
        const payload = JSON.stringify({
            type,
            path: `${location.pathname}${location.search}`,
            referrer_path: document.referrer,
            page_title: document.title,
            ...details,
        });
        // keepalive отправляет события при уходе со страницы и передаёт CSRF.
        // sendBeacon не поддерживает произвольные заголовки для CSRF.
        fetch(endpoint, {
            method: 'POST', credentials: 'same-origin', keepalive: true,
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
            body: payload,
        }).catch(() => {});
    };
    window.aureneAnalytics = {track: send};

    send('page_view');
    const catalog = document.querySelector('[data-analytics-catalog]');
    if (catalog) {
        const brand = catalog.dataset.analyticsBrand || '';
        const category = catalog.dataset.analyticsCategory || '';
        send(category ? 'category_view' : brand ? 'brand_view' : 'catalog_view', {brand, category});
        const params = new URLSearchParams(location.search);
        if (params.get('q')) send('internal_search', {metadata: {
            query: params.get('q'), result_count: Number(catalog.dataset.analyticsResults) || 0,
        }});
        const filterKeys = ['gender', 'category', 'brand', 'color', 'material', 'price_min', 'price_max', 'collection'];
        const filters = Object.fromEntries(filterKeys.filter(key => params.has(key)).map(key => [key, params.get(key)]));
        if (Object.keys(filters).length) send('filter_use', {metadata: {filters}});
        if (params.get('ordering')) send('sort_use', {metadata: {ordering: params.get('ordering')}});
    }
    if (productRoot) send('product_view', productData());

    const bindProductClicks = (root = document) => root.querySelectorAll('[data-analytics-product-click]').forEach(link => {
        link.addEventListener('click', () => send('product_click', productData(link), true));
    });
    bindProductClicks();
    document.addEventListener('catalog:more', (event) => {
        event.detail.cards.forEach(bindProductClicks);
    });
    document.querySelectorAll('[data-gallery-thumb]').forEach((button, index) => {
        button.addEventListener('click', () => send('product_photo_view', {
            ...productData(), metadata: {photo_index: index + 1},
        }));
    });
    document.querySelectorAll('[data-size-option]').forEach(input => {
        input.addEventListener('change', () => send('size_select', {
            ...productData(), metadata: {size_sku: input.value, size: input.nextElementSibling?.textContent || ''},
        }));
    });
    document.querySelectorAll('[data-variant-color-list] a').forEach(link => {
        link.addEventListener('click', () => send('color_select', {
            ...productData(), metadata: {color: link.title || ''},
        }, true));
    });

    const requestForm = document.querySelector('[data-request-form]');
    if (requestForm) {
        let started = false;
        requestForm.addEventListener('input', event => {
            if (!started) { send('personal_order_start'); started = true; }
            const name = event.target.name || '';
            if (name.endsWith('-brand')) send('personal_order_brand');
            else if (name.endsWith('-product_name')) send('personal_order_model');
            else if (/-(article|color|size)$/.test(name)) send('personal_order_details');
        });
        requestForm.querySelector('[data-add-item]')?.addEventListener('click', () => send('personal_order_add_item'));
    }

    const checkout = document.querySelector('.checkout-form');
    if (checkout) {
        checkout.querySelector('[name="delivery_address"]')?.addEventListener('change', () => send('checkout_delivery'));
        checkout.addEventListener('submit', () => send('checkout_contacts', {}, true));
    }

    const checkScroll = () => {
        const available = document.documentElement.scrollHeight - innerHeight;
        const percent = available <= 0 ? 100 : Math.round(scrollY / available * 100);
        [25, 50, 75, 100].forEach(mark => {
            if (percent >= mark && !sentScroll.has(mark)) {
                sentScroll.add(mark);
                send(`scroll_${mark}`);
            }
        });
    };
    addEventListener('scroll', checkScroll, {passive: true});
    checkScroll();
    addEventListener('pagehide', () => send('session_engaged', {
        metadata: {duration_seconds: Math.round((Date.now() - startedAt) / 1000)},
    }, true));
});