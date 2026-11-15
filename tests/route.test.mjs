// Run with: node tests/route.test.mjs   (also run by tests/test_route.py when node is installed)
import assert from "node:assert/strict";
import { deviceFor, routePlanRepair, KEYBOARD_WORDS } from "../training/viewer/route.js";

// the keyboard words, as whole words only
for (const t of ["keyboard", "my keyboard", "RK68", "rk 61", "RK84", "the J key is dead", "space bar rattles", "a keycap fell off", "Royal Kludge keyboard", "my keys are sticky", "Logitech MX Keys"])
  assert.equal(deviceFor(t), "keyboard", `keyboard: ${t}`);
for (const t of ["monkey", "turkey", "keyless entry fob", "hockey stick"])
  assert.equal(KEYBOARD_WORDS.test(t), false, `not a keyboard: ${t}`);

// the other built-in devices
for (const t of ["iPhone 15", "my iPhone", "I phone", "iphone screen is cracked"]) assert.equal(deviceFor(t), "iphone15", t);
for (const t of ["iPad", "my iPad is broken", "i pad"]) assert.equal(deviceFor(t), "ipad11", t);
assert.equal(deviceFor("Sennheiser Momentum 4"), "momentum4");
for (const t of ["three pin plug", "the fuse in my plug", "UK 13A plug", "mains plug is hot", "my plug is broken"]) assert.equal(deviceFor(t), "plug13a", t);
assert.equal(deviceFor("Dyson V8, the plug is loose"), null, "a model number keeps it an object");
assert.equal(deviceFor("keyboard plug"), "keyboard", "keyboard words win");

// everything else is learned live (3D model + questions): headphones in general are NOT the Momentum
for (const t of ["Sony WH-1000XM5 headphones", "Bose QuietComfort Ultra headphones", "wireless earphones", "AirPods Pro 2, the right earbud is quiet", "Dyson V8 won't charge",
  "Nintendo Switch joy-con drifts", "Kindle Paperwhite", "Logitech MX Master 3S", "Apple Watch Series 9", "PlayStation 5 controller", "my toaster", "Samsung Galaxy Tab S9"])
  assert.equal(deviceFor(t), null, `object: ${t}`);

// first screen: the words decide
const first = (text) => routePlanRepair({ text, pickOpen: true, objectOn: false, asking: false });
assert.equal(first("keyboard"), "keyboard");
assert.equal(first("my J key is dead"), "keyboard");
assert.equal(first("iPad"), "ipad11");
assert.equal(first("AirPods Pro 2, the right earbud is quiet"), "object");
assert.equal(first("Sony WH-1000XM5 headphones"), "object");

// once the keyboard is chosen, every fault stays in the keyboard pipeline: never an object interview
const inKeyboard = (text) => routePlanRepair({ text, pickOpen: false, objectOn: false, asking: false });
for (const t of ["the switch is sticky", "the backlight is dead", "it won't turn on", "the usb port is loose", "the stabilizer rattles", "the battery drains", "F key", "AirPods Pro 2"])
  assert.equal(inKeyboard(t), "keyboard", `stays keyboard: ${t}`);

// while Spanner interviews about an object, what the person says is an answer, even if it mentions a key
assert.equal(routePlanRepair({ text: "the left button keys feel mushy", pickOpen: false, objectOn: true, asking: true }), "answer");
assert.equal(routePlanRepair({ text: "AirPods Pro 2", pickOpen: true, objectOn: true, asking: true }), "answer");

// object mode, interview over: an object complaint stays an object, a keyboard one goes to the keyboard
assert.equal(routePlanRepair({ text: "the earbud crackles", pickOpen: false, objectOn: true, asking: false }), "object");
assert.equal(routePlanRepair({ text: "my keyboard J key is dead", pickOpen: false, objectOn: true, asking: false }), "keyboard");
console.log("route: all scenarios pass");
