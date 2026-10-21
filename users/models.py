from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.functions import Lower


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("У пользователя должен быть email")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        # Банковская роль: суперпользователь, как и раньше, не подаёт заявки от имени бизнеса.
        extra.setdefault("role", User.Role.BANK_ADMIN)
        return self.create_user(email, password, **extra)

    def get_by_natural_key(self, email):
        """Так человека ищет вход. Без учёта регистра: буквы в адресе никто не помнит."""
        return self.get(email__iexact=email)


class User(AbstractUser):
    class Role(models.TextChoices):
        BUSINESS = "BUSINESS", "Представитель бизнеса"
        BANK_REVIEWER = "BANK_REVIEWER", "Кредитный специалист"
        SENIOR_APPROVER = "SENIOR_APPROVER", "Старший согласующий"
        BANK_ADMIN = "BANK_ADMIN", "Администратор банка"

    # Логин — email, отдельного имени пользователя нет.
    username = None
    email = models.EmailField("email", unique=True)
    role = models.CharField("роль", max_length=32, choices=Role.choices, default=Role.BUSINESS)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"
        ordering = ("email",)
        constraints = [
            # unique у поля различает регистр, а save() обходят update() и bulk_create.
            models.UniqueConstraint(
                Lower("email"),
                name="users_user_email_ci",
                violation_error_message="Пользователь с таким email уже зарегистрирован",
            ),
        ]

    def save(self, *args, **kwargs):
        # Адрес — логин: храним строчными, каким бы путём человека ни завели.
        self.email = self.email.lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email
