// The number a bar-chart value starts with, which the bar's length follows: Arabic digits 0-9, with commas between
// groups of three and a decimal part ("1,200 元" is 1200, "2.5 倍" 2.5), the spaces at the value's ends left out;
// null when the value does not start with one, or when the number goes on with a digit, or with a comma or a point
// and a digit ("2,5 倍", "1,2345", "1.5.3"). pipeline/scenes.py (bar_number) reads a value the same way, and validate
// makes sure every value of a rendered card starts with a number and one is above 0.

const BAR_NUMBER = /^[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?/;
const GOES_ON = /^(?:[0-9]|[,.][0-9])/;

export function barNumber(value) {
  const text = value.trim();
  const match = BAR_NUMBER.exec(text);
  if (match === null || GOES_ON.test(text.slice(match[0].length))) {
    return null;
  }
  return Number(match[0].replaceAll(",", ""));
}
