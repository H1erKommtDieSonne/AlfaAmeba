from django import forms


INPUT_CLASS = "form-control"


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        if not files or files == [None]:
            if self.required:
                raise forms.ValidationError(self.error_messages["required"], code="required")
            return []
        return [super().clean(file, initial) for file in files]
