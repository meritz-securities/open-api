import {test,expect} from '@playwright/test';
import {percent,tone} from '../src/api';

test('percent preserves small nonzero signs and finite values',()=>{
 expect(percent(0.00024726038789779636)).toBe('+0.000247%');
 expect(percent(-0.00012305820799178718)).toBe('-0.000123%');
 expect(percent(0.00499)).toBe('+0.00499%');
 expect(percent(-0.00499)).toBe('-0.00499%');
 expect(percent(0.004999)).toBe('+0.00500%');
 expect(percent(0.005)).toBe('+0.01%');
 expect(percent(-0.005)).toBe('-0.01%');
 expect(percent(12.345)).toBe('+12.35%');
 expect(percent(-12.345)).toBe('-12.35%');
 expect(percent(1e-10)).toBe('+1.00e-10%');
 expect(percent(-1e-10)).toBe('-1.00e-10%');
 expect(percent(Number.MIN_VALUE)).toBe('+4.94e-324%');
 expect(percent(-Number.MIN_VALUE)).toBe('-4.94e-324%');
 expect(percent(0)).toBe('0.00%');
 expect(percent(-0)).toBe('0.00%');
 for(const value of [null,undefined,NaN,Infinity,-Infinity])expect(percent(value)).toBe('—');
});

test('tone follows every finite nonzero sign, including tiny returns',()=>{
 for(const value of [Number.MIN_VALUE,1e-10,.00499,.005,12.345])expect(tone(value)).toBe('up');
 for(const value of [-Number.MIN_VALUE,-1e-10,-.00499,-.005,-12.345])expect(tone(value)).toBe('down');
 for(const value of [0,-0,null,undefined,NaN,Infinity,-Infinity])expect(tone(value)).toBe('');
});
