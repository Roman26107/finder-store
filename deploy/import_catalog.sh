#!/bin/sh
set -eu

FIXTURE="${1:-/app/imports/catalog.json.gz}"

if [ ! -f "$FIXTURE" ]; then
  echo "Не найден fixture: $FIXTURE" >&2
  exit 1
fi

PRODUCTS="$(python manage.py shell -c "from shop.models import Product; print(Product.objects.count())" | tail -n 1)"
if [ "$PRODUCTS" != "0" ]; then
  echo "Импорт разрешён только в пустой каталог; сейчас товаров: $PRODUCTS" >&2
  exit 1
fi

python manage.py loaddata "$FIXTURE"
python manage.py shell -c "from shop.models import Product, ProductImage; print(f'Импортировано товаров: {Product.objects.count()}, изображений: {ProductImage.objects.count()}')"