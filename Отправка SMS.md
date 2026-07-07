# Отправка SMS

### 1. Создать правило `send_sms.js`

```javascript
// send_sms.js

defineVirtualDevice("sms_sender", {
  title: {
    'en': 'SMS Sender',
    'ru': 'Отправка SMS'
  },
  cells: {
    send: {
      title: {
        'en': 'Send',
        'ru': 'Отправить'
      },
      type: "text",
      readonly: false,
      value: ""
    },
    last_sent_time: {
      title: {
        'en': 'Last Sent Time',
        'ru': 'Время последней отправки'
      },
      type: "text",
      readonly: true,
      value: ""
    },
    last_message_text: {
      title: {
        'en': 'Last Message',
        'ru': 'Последнее сообщение'
      },
      type: "text",
      readonly: true,
      value: ""
    },
    last_recipient_number: {
      title: {
        'en': 'Last Recipient Number',
        'ru': 'Последний номер получателя'
      },
      type: "text",
      readonly: true,
      value: ""
    }
  }
});

defineRule("send_sms_via_notify", {
  whenChanged: "sms_sender/send",
  then: function (newValue, devName, cellName) {
    // Проверка на пустое значение
    if (!newValue) {
      log("Получено пустое значение");
      return;
    }

    // Ожидаемый формат: "+НомерТелефона;Текст сообщения"
    var data = newValue.split(';');
    if (data.length < 2) {
      log("Неверный формат SMS. Ожидается '+Номер;Сообщение'");
      return;
    }

    var phoneNumber = data[0];
    var messageText = data.slice(1).join(';');
    messageText = messageText
      .replace(/\r\n/g, "\r\n")
      .replace(/\n/g, "\n");
    log("Отправка SMS на " + phoneNumber + " с сообщением: " + messageText);

    try {
      Notify.sendSMS(phoneNumber, messageText);
      log("SMS успешно отправлено");
      
      var currentTime = new Date().toISOString();
      dev["sms_sender"]["last_sent_time"] = currentTime;
      dev["sms_sender"]["last_message_text"] = messageText;
      dev["sms_sender"]["last_recipient_number"] = phoneNumber;
    } catch(e) {
      log("Ошибка отправки SMS: " + e);
    }
  }
});
```


### **2. Добавляем новые сенсоры в Home Assistant**

Добавить следующие сенсоры в `configuration.yaml` и перезагрузить Home Assistant:

```yaml
mqtt:
  sensor:
    - name: "Operator"
      state_topic: "/devices/system__networks__5d4297ba-c319-4c05-a153-17cb42e6e196/controls/Operator"
      unique_id: wb_operator
      icon: "mdi:sim"
      device:
        identifiers: ["wb_modem_1"]
        name: "WB Modem 1"
        manufacturer: "Wiren Board"
        model: "Mobile Network"
    - name: "LTE Signal"
      state_topic: "/devices/system__networks__5d4297ba-c319-4c05-a153-17cb42e6e196/controls/SignalQuality"
      unique_id: wb_signal_quality
      device_class: "signal_strength"
      unit_of_measurement: "dBm"
      icon: "mdi:signal"
      device:
        identifiers: ["wb_modem_1"]
        name: "WB Modem 1"
        manufacturer: "Wiren Board"
        model: "Mobile Network"
    - name: "IP"
      state_topic: "/devices/network/controls/GPRS IP"
      unique_id: wb_gprs_ip
      icon: "mdi:ip-network"
      device:
        identifiers: ["wb_modem_1"]
        name: "WB Modem 1"
        manufacturer: "Wiren Board"
        model: "Mobile Network"
    - name: "LTE Status"
      state_topic: "/devices/network/controls/GPRS IP Online Status"
      unique_id: wb_gprs_status
      icon: "mdi:signal-4g"
      value_template: >-
        {% if value == '1' %}
          Connected
        {% else %}
          Disconnected
        {% endif %}
      device:
        identifiers: ["wb_modem_1"]
        name: "WB Modem 1"
        manufacturer: "Wiren Board"
        model: "Mobile Network"
    - name: "LTE Connection"
      state_topic: "/devices/network/controls/GPRS IP Connection Enabled"
      unique_id: wb_gprs_connection
      icon: "mdi:web"
      value_template: >-
        {% if value == '1' %}
          Enabled
        {% else %}
          Disabled
        {% endif %}
      device:
        identifiers: ["wb_modem_1"]
        name: "WB Modem 1"
        manufacturer: "Wiren Board"
        model: "Mobile Network"
    - name: "Последняя отправка"
      state_topic: "/devices/sms_sender/controls/last_sent_time"
      unique_id: wb_sms_last_sent_time
      icon: "mdi:clock-outline"
      device_class: timestamp
      device:
        identifiers: ["wb_sms"]
        name: "WB SMS Sender"
        manufacturer: "Wiren Board"
        model: "SMS Sender"
    - name: "Последнее сообщение"
      state_topic: "/devices/sms_sender/controls/last_message_text"
      unique_id: wb_sms_last_message_text
      icon: "mdi:message-text-outline"
      device:
        identifiers: ["wb_sms"]
        name: "WB SMS Sender"
        manufacturer: "Wiren Board"
        model: "SMS Sender"
    - name: "Последний получатель"
      state_topic: "/devices/sms_sender/controls/last_recipient_number"
      unique_id: wb_sms_last_recipient_number
      icon: "mdi:phone-outline"
      device:
        identifiers: ["wb_sms"]
        name: "WB SMS Sender"
        manufacturer: "Wiren Board"
        model: "SMS Sender"
```


### **3. Настройка отправки SMS из Home Assistant**

Создать в `packages` файл `notify_sms.yaml`:

```yaml
# packages/notify_sms.yaml
notify_sms:
  script:
    send_sms_notification:
      alias: "Отправить SMS"
      sequence:
        - service: mqtt.publish
          data:
            topic: "/devices/sms_sender/controls/send/on"
            payload_template: "{{ data.phone }};{{ message }}"
```


### #**4. Использование скрипта для отправки SMS**

Можно запускать скрипт вручную или использовать его в автоматизациях.

Пример запуска вручную:

```yaml
action: script.send_sms_notification
data:
  message: "У кошки в доме {{ states('sensor.0x38398ffffede3088_temperature') | round(1) }}°C\nВлажность - {{ states('sensor.0x38398ffffede3088_humidity') | round(0) }}%"
  data:
    phone: "+79657997779"
```

Пример запуска в автоматизации:

```yaml
automation:
  - alias: "SMS при открытии двери"
    trigger:
      - platform: state
        entity_id: binary_sensor.door
        to: "on"
    action:
      - service: script.send_sms_notification
        data:
          message: "Дверь открыта!"
          data:
            phone: "+79657997779"
```

### #**5. Дополнительно**

- В целях безопасности можно хранить номер телефона в файле `secrets.yaml` в Home Assistant:

  ```yaml
  # secrets.yaml
  sms_recipient_number: "+79657997779"
  ```

  И использовать в скрипте:

  ```yaml
  data:
    number: !secret sms_recipient_number
    message: "Текст сообщения"
  ```


