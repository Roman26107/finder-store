from django import forms

from .models import ManagerContact


class ManagerContactForm(forms.ModelForm):
    class Meta:
        model = ManagerContact
        fields = ('position', 'kind', 'label', 'value', 'url', 'is_active')
        widgets = {
            'position': forms.NumberInput(attrs={'min': 0}),
            'value': forms.Textarea(attrs={'rows': 2}),
            'url': forms.URLInput(attrs={'placeholder': 'https://... (необязательно)'}),
        }


ManagerContactFormSet = forms.modelformset_factory(
    ManagerContact,
    form=ManagerContactForm,
    fields=('position', 'kind', 'label', 'value', 'url', 'is_active'),
    extra=1,
    can_delete=True,
)