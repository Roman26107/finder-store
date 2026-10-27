document.addEventListener('DOMContentLoaded', () => {
    const readData = (canvas) => {
        const element = document.getElementById(canvas.dataset.chartSource);
        if (!element) return null;
        try { return JSON.parse(element.textContent); } catch (error) { return null; }
    };

    const prepareCanvas = (canvas) => {
        const rect = canvas.getBoundingClientRect();
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.max(1, Math.round(rect.width * ratio));
        canvas.height = Math.max(1, Math.round(rect.height * ratio));
        const context = canvas.getContext('2d');
        context.setTransform(ratio, 0, 0, ratio, 0, 0);
        return {context, width: rect.width, height: rect.height};
    };

    const drawEmpty = (context, width, height) => {
        context.fillStyle = '#8a847c';
        context.font = '12px Manrope, sans-serif';
        context.textAlign = 'center';
        context.fillText('Данные появятся после накопления статистики', width / 2, height / 2);
    };

    const drawLine = (canvas, data) => {
        const {context, width, height} = prepareCanvas(canvas);
        const padding = {top: 18, right: 20, bottom: 38, left: 42};
        const values = data.series.flatMap((series) => series.values);
        if (!values.length || values.every((value) => value === 0)) return drawEmpty(context, width, height);
        const max = Math.max(...values, 0);
        const min = Math.min(...values, 0);
        const range = Math.max(max - min, 1);
        const plotWidth = width - padding.left - padding.right;
        const plotHeight = height - padding.top - padding.bottom;
        const formatAxis = (value) => new Intl.NumberFormat('ru-RU', {
            notation: Math.abs(value) >= 10000 ? 'compact' : 'standard',
            maximumFractionDigits: 1,
        }).format(value);
        context.font = '9px Manrope, sans-serif';
        context.lineWidth = 1;
        for (let line = 0; line <= 4; line += 1) {
            const y = padding.top + plotHeight * line / 4;
            const axisValue = max - range * line / 4;
            context.strokeStyle = Math.abs(axisValue) < range / 100 ? '#aaa39a' : '#e7e2dc';
            context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
            context.fillStyle = '#8a847c'; context.textAlign = 'right';
            context.fillText(formatAxis(axisValue), padding.left - 9, y + 3);
        }
        const labelStep = Math.max(1, Math.ceil(data.labels.length / 8));
        data.labels.forEach((label, index) => {
            if (index % labelStep && index !== data.labels.length - 1) return;
            const x = padding.left + plotWidth * index / Math.max(data.labels.length - 1, 1);
            context.fillStyle = '#8a847c'; context.textAlign = 'center';
            context.fillText(label, x, height - 12);
        });
        const points = [];
        data.series.forEach((series) => {
            context.strokeStyle = series.color; context.lineWidth = 2.2;
            context.beginPath();
            series.values.forEach((value, index) => {
                const x = padding.left + plotWidth * index / Math.max(series.values.length - 1, 1);
                const y = padding.top + (max - value) / range * plotHeight;
                if (!index) context.moveTo(x, y); else context.lineTo(x, y);
                points.push({x, y, value, label: data.labels[index], series: series.label, color: series.color});
            });
            context.stroke();
        });
        canvas._chartPoints = points;
    };

    const drawDonut = (canvas, data) => {
        const {context, width, height} = prepareCanvas(canvas);
        const total = data.values.reduce((sum, value) => sum + value, 0);
        if (!total) return drawEmpty(context, width, height);
        const centerX = width / 2, centerY = height / 2;
        const radius = Math.min(width, height) * .36;
        let start = -Math.PI / 2;
        data.values.forEach((value, index) => {
            const end = start + value / total * Math.PI * 2;
            context.beginPath(); context.arc(centerX, centerY, radius, start, end);
            context.strokeStyle = data.colors[index] || '#aaa'; context.lineWidth = 24; context.stroke();
            start = end;
        });
    };

    const drawBars = (canvas, data) => {
        const {context, width, height} = prepareCanvas(canvas);
        const max = Math.max(...data.values, 0);
        if (!max) return drawEmpty(context, width, height);
        const padding = {top: 12, right: 20, bottom: 48, left: 24};
        const available = width - padding.left - padding.right;
        const gap = Math.max(8, available * .035);
        const barWidth = Math.max(12, (available - gap * (data.values.length - 1)) / data.values.length);
        data.values.forEach((value, index) => {
            const x = padding.left + index * (barWidth + gap);
            const barHeight = (height - padding.top - padding.bottom) * value / max;
            context.fillStyle = data.colors[index] || '#9b7955';
            context.fillRect(x, height - padding.bottom - barHeight, barWidth, barHeight);
            context.fillStyle = '#181715'; context.font = '600 10px Manrope, sans-serif'; context.textAlign = 'center';
            context.fillText(String(value), x + barWidth / 2, height - padding.bottom - barHeight - 7);
            context.fillStyle = '#77716a'; context.font = '8px Manrope, sans-serif';
            const label = String(data.labels[index] || '');
            context.fillText(label.length > 12 ? `${label.slice(0, 11)}…` : label, x + barWidth / 2, height - 24);
        });
    };

    const renderChart = (canvas) => {
        const data = readData(canvas);
        if (!data) return;
        if (canvas.dataset.analyticsChart === 'line') drawLine(canvas, data);
        if (canvas.dataset.analyticsChart === 'donut') drawDonut(canvas, data);
        if (canvas.dataset.analyticsChart === 'bar') drawBars(canvas, data);
    };
    const renderCharts = () => document.querySelectorAll('[data-analytics-chart]').forEach(renderChart);
    renderCharts();
    let resizeTimer;
    addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(renderCharts, 120);
    });

    document.querySelectorAll('[data-analytics-chart="line"]').forEach((canvas) => {
        const tooltip = canvas.parentElement.querySelector('[data-chart-tooltip]');
        canvas.addEventListener('mousemove', (event) => {
            if (!tooltip || !canvas._chartPoints?.length) return;
            const rect = canvas.getBoundingClientRect();
            const x = event.clientX - rect.left, y = event.clientY - rect.top;
            const point = canvas._chartPoints.reduce((best, candidate) => {
                const distance = Math.hypot(candidate.x - x, candidate.y - y);
                return !best || distance < best.distance ? {candidate, distance} : best;
            }, null);
            if (!point || point.distance > 35) { tooltip.hidden = true; return; }
            tooltip.innerHTML = `<b>${point.candidate.label}</b><span>${point.candidate.series}: ${point.candidate.value}</span>`;
            tooltip.style.left = `${Math.min(point.candidate.x + 10, rect.width - 130)}px`;
            tooltip.style.top = `${Math.max(5, point.candidate.y - 50)}px`;
            tooltip.hidden = false;
        });
        canvas.addEventListener('mouseleave', () => { if (tooltip) tooltip.hidden = true; });
    });

    document.querySelectorAll('[data-chart-switch]').forEach((button) => {
        button.addEventListener('click', () => {
            const canvas = document.querySelector('[data-switchable-chart]');
            if (!canvas || !document.getElementById(button.dataset.chartSwitch)) return;
            document.querySelectorAll('[data-chart-switch]').forEach((item) => {
                item.classList.toggle('active', item === button);
            });
            canvas.dataset.chartSource = button.dataset.chartSwitch;
            canvas._chartPoints = [];
            const tooltip = canvas.parentElement.querySelector('[data-chart-tooltip]');
            if (tooltip) tooltip.hidden = true;
            renderChart(canvas);
        });
    });

    document.querySelectorAll('[data-sortable-table]').forEach((table) => {
        table.querySelectorAll('[data-sort-column]').forEach((button) => {
            button.addEventListener('click', () => {
                const column = Number(button.dataset.sortColumn);
                const numeric = button.dataset.sortType === 'number';
                const ascending = button.dataset.sortDirection !== 'asc';
                table.querySelectorAll('[data-sort-column]').forEach((item) => {
                    item.removeAttribute('data-sort-direction'); item.removeAttribute('aria-sort');
                });
                button.dataset.sortDirection = ascending ? 'asc' : 'desc';
                button.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
                const rows = Array.from(table.tBodies[0]?.rows || []).filter((row) => row.cells.length > 1);
                rows.sort((left, right) => {
                    const leftCell = left.cells[column], rightCell = right.cells[column];
                    const leftValue = leftCell?.dataset.sortValue || leftCell?.textContent.trim() || '';
                    const rightValue = rightCell?.dataset.sortValue || rightCell?.textContent.trim() || '';
                    const comparison = numeric
                        ? (Number(String(leftValue).replace(',', '.')) || 0) - (Number(String(rightValue).replace(',', '.')) || 0)
                        : leftValue.localeCompare(rightValue, 'ru', {numeric: true, sensitivity: 'base'});
                    return ascending ? comparison : -comparison;
                });
                rows.forEach((row) => table.tBodies[0].append(row));
            });
        });
    });
});