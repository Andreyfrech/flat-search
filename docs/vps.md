# Сбор данных с сервера в России

Циан блокирует запросы с зарубежных серверов, в том числе с серверов GitHub. Поэтому сам сбор идёт на небольшом арендованном сервере (VPS) в России, а GitHub хранит код, расписание и страницу. Сервер работает круглосуточно, ваш компьютер для этого не нужен.

## 1. Арендовать сервер

Подойдёт самый дешёвый тариф российского хостинга (Timeweb Cloud, Selectel, Beget, VDSina и т. п.):
- система **Ubuntu 24.04** (или 22.04), локация **Москва**;
- 1 ядро, 1 ГБ памяти — с запасом;
- лучше посуточная или почасовая оплата: если Циан не пустит и этот сервер, его можно сразу удалить.

После создания хостинг покажет **IP-адрес** и **пароль root**.

## 2. Подключиться к серверу

В Windows откройте PowerShell и выполните (вместо `IP` — адрес сервера):

```
ssh root@IP
```

На вопрос `Are you sure you want to continue connecting` ответьте `yes`, затем вставьте пароль (при вводе он не отображается — это нормально).

## 3. Проверить, что Циан отвечает серверу

```
curl -fsSL https://raw.githubusercontent.com/Andreyfrech/flat-search/main/scripts/setup-server.sh -o setup.sh && bash setup.sh check
```

- **«ЦИАН ОТВЕЧАЕТ: найдено …»** — всё в порядке, переходите к шагу 4.
- **«НЕ РАБОТАЕТ»** — этот хостинг Циан тоже блокирует. Удалите сервер и попробуйте другого провайдера.

## 4. Подключить сервер к GitHub

1. Откройте https://github.com/Andreyfrech/flat-search/settings/actions/runners/new
2. В блоке **Configure** найдите строку с `--token` и скопируйте токен — длинную строку после `--token` (она действует 1 час).
3. На сервере выполните, вставив токен:

```
bash setup.sh install ТОКЕН
```

В конце должно появиться «ГОТОВО». На странице https://github.com/Andreyfrech/flat-search/settings/actions/runners сервер `cian-vps` будет со статусом **Idle**.

## 5. Переключить обновление на сервер

1. Откройте https://github.com/Andreyfrech/flat-search/settings/variables/actions/new
2. **Name**: `RUNNER`, **Value**: `self-hosted` → **Add variable**.
3. Откройте https://github.com/Andreyfrech/flat-search/actions/workflows/update.yml и нажмите **Run workflow**, чтобы не ждать расписания.

Через пару минут обновятся данные на https://andreyfrech.github.io/flat-search/. Дальше всё идёт само каждые 2 дня.
