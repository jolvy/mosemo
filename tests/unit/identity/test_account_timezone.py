from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.events import AccountTimezoneChanged
from mosemo.shared.domain.timezones import Timezone


def seoul_account() -> Account:
    account = Account(provider=AccountProvider.KAKAO, provider_subject="subject")
    account.timezone = Timezone.ASIA_SEOUL
    return account


def test_change_timezone_records_the_previous_and_current_timezone() -> None:
    account = seoul_account()

    account.change_timezone(Timezone.AMERICA_NEW_YORK)

    assert account.timezone is Timezone.AMERICA_NEW_YORK
    assert account.events == [
        AccountTimezoneChanged(
            account_id=account.id,
            previous=Timezone.ASIA_SEOUL,
            current=Timezone.AMERICA_NEW_YORK,
        )
    ]


def test_change_timezone_to_the_same_timezone_changes_nothing() -> None:
    account = seoul_account()

    account.change_timezone(Timezone.ASIA_SEOUL)

    assert account.timezone is Timezone.ASIA_SEOUL
    assert account.events == []
