"use strict";

function nsDataToHex(nsdata) {
  const length = nsdata.length().valueOf();
  const bytes = nsdata.bytes();
  if (length === 0) return "";
  const raw = Memory.readByteArray(bytes, length);
  const arr = new Uint8Array(raw);
  let out = "";
  for (let i = 0; i < arr.length; i++) {
    out += ("0" + arr[i].toString(16)).slice(-2);
  }
  return out;
}

function safeUuidString(obj) {
  try {
    if (!obj || obj.isNull()) return "<null>";
    const uuid = new ObjC.Object(obj).UUID();
    return uuid ? uuid.UUIDString().toString() : "<no-uuid>";
  } catch (e) {
    return `<uuid-error:${e}>`;
  }
}

function safeDesc(obj) {
  try {
    if (!obj || obj.isNull()) return "<null>";
    return new ObjC.Object(obj).toString();
  } catch (e) {
    return `<desc-error:${e}>`;
  }
}

if (!ObjC.available) {
  console.log("ObjC runtime not available");
} else {
  const target = ObjC.classes.CBPeripheral["- writeValue:forCharacteristic:type:"];
  if (!target) {
    console.log("CBPeripheral hook target not found");
  } else {
    console.log("Hooking CBPeripheral writeValue:forCharacteristic:type:");
    Interceptor.attach(target.implementation, {
      onEnter(args) {
        try {
          const data = new ObjC.Object(args[2]);
          const characteristic = args[3];
          const writeType = args[4].toInt32();
          const hex = nsDataToHex(data);
          const charUuid = safeUuidString(characteristic);
          const charDesc = safeDesc(characteristic);
          const line = JSON.stringify({
            event: "ble_write",
            char_uuid: charUuid,
            write_type: writeType,
            data_len: data.length().valueOf(),
            hex,
            characteristic: charDesc,
          });
          console.log(line);
        } catch (e) {
          console.log(`hook_error:${e}`);
        }
      },
    });
  }
}
