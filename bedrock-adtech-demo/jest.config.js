module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src'],
  testMatch: ['**/*.spec.ts', '**/*.test.ts'],
  moduleNameMapper: {
    '^src/(.*)$': '<rootDir>/src/$1',
    // uuid 14 is ESM-only; see src/test-support/uuid-shim.ts
    '^uuid$': '<rootDir>/src/test-support/uuid-shim.ts'
  },
  transform: {
    '^.+\\.tsx?$': ['ts-jest', {
      tsconfig: 'tsconfig.json',
      diagnostics: false
    }]
  }
};
