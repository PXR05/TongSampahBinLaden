/**
 * JavaScript AES encryption utility for web dashboard
 * Provides client-side encryption capabilities for sending encrypted commands to server
 *
 * This is for future use - currently the dashboard uses unencrypted HTTP Basic Auth
 * but this utility allows upgrading to encrypted communication if needed.
 */

class DashboardCrypto {
  constructor() {
    // AES-256 key (must match server configuration)
    // In production, this should be loaded securely or derived from user credentials
    this.aesKey = new Uint8Array([
      0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6,
      0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c,
      0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6,
      0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c
    ]);

    this.encoder = new TextEncoder();
    this.decoder = new TextDecoder();
  }

  /**
   * Generate random initialization vector
   * @returns {Uint8Array} 16-byte random IV
   */
  generateIV() {
    return crypto.getRandomValues(new Uint8Array(16));
  }

  /**
   * Convert ArrayBuffer to base64 string
   * @param {ArrayBuffer} buffer
   * @returns {string} Base64 encoded string
   */
  arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  }

  /**
   * Convert base64 string to ArrayBuffer
   * @param {string} base64
   * @returns {ArrayBuffer} Decoded buffer
   */
  base64ToArrayBuffer(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
  }

  /**
   * Encrypt plaintext using AES-256-CBC
   * @param {string} plaintext - Text to encrypt
   * @returns {Promise<string>} Base64 encoded IV + ciphertext
   */
  async encrypt(plaintext) {
    try {
      // Convert string to bytes
      const data = this.encoder.encode(plaintext);

      // Generate random IV
      const iv = this.generateIV();

      // Import AES key
      const cryptoKey = await crypto.subtle.importKey(
        'raw',
        this.aesKey,
        { name: 'AES-CBC' },
        false,
        ['encrypt']
      );

      // Encrypt data
      const encrypted = await crypto.subtle.encrypt(
        { name: 'AES-CBC', iv: iv },
        cryptoKey,
        data
      );

      // Combine IV + encrypted data
      const combined = new Uint8Array(iv.length + encrypted.byteLength);
      combined.set(iv, 0);
      combined.set(new Uint8Array(encrypted), iv.length);

      // Return base64 encoded result
      return this.arrayBufferToBase64(combined);

    } catch (error) {
      console.error('Encryption failed:', error);
      throw new Error('Encryption failed: ' + error.message);
    }
  }

  /**
   * Decrypt ciphertext using AES-256-CBC
   * @param {string} ciphertext - Base64 encoded IV + encrypted data
   * @returns {Promise<string>} Decrypted plaintext
   */
  async decrypt(ciphertext) {
    try {
      // Decode base64
      const combined = new Uint8Array(this.base64ToArrayBuffer(ciphertext));

      // Extract IV and encrypted data
      if (combined.length < 32) { // 16 bytes IV + at least 16 bytes data
        throw new Error('Ciphertext too short');
      }

      const iv = combined.slice(0, 16);
      const encrypted = combined.slice(16);

      // Import AES key
      const cryptoKey = await crypto.subtle.importKey(
        'raw',
        this.aesKey,
        { name: 'AES-CBC' },
        false,
        ['decrypt']
      );

      // Decrypt data
      const decrypted = await crypto.subtle.decrypt(
        { name: 'AES-CBC', iv: iv },
        cryptoKey,
        encrypted
      );

      // Convert back to string
      return this.decoder.decode(decrypted);

    } catch (error) {
      console.error('Decryption failed:', error);
      throw new Error('Decryption failed: ' + error.message);
    }
  }

  /**
   * Encrypt JSON object
   * @param {Object} data - Object to encrypt
   * @returns {Promise<string>} Base64 encoded encrypted JSON
   */
  async encryptJSON(data) {
    const jsonString = JSON.stringify(data);
    return await this.encrypt(jsonString);
  }

  /**
   * Decrypt JSON object
   * @param {string} ciphertext - Base64 encoded encrypted JSON
   * @returns {Promise<Object>} Decrypted object
   */
  async decryptJSON(ciphertext) {
    const plaintext = await this.decrypt(ciphertext);
    return JSON.parse(plaintext);
  }

  /**
   * Create encrypted request wrapper
   * @param {Object} data - Data to encrypt and wrap
   * @returns {Promise<Object>} Wrapped encrypted data
   */
  async createEncryptedRequest(data) {
    const encrypted = await this.encryptJSON(data);
    return { encrypted: encrypted };
  }

  /**
   * Extract encrypted data from response
   * @param {Object} response - Response with encrypted data
   * @returns {Promise<Object>} Decrypted data
   */
  async extractEncryptedResponse(response) {
    if (!response || !response.encrypted) {
      throw new Error('No encrypted data found in response');
    }
    return await this.decryptJSON(response.encrypted);
  }
}

// Global instance for use in dashboard
const dashboardCrypto = new DashboardCrypto();

/**
 * Enhanced fetch function that can send encrypted requests
 * @param {string} url - Request URL
 * @param {Object} options - Fetch options
 * @param {boolean} encrypt - Whether to encrypt the request body
 * @returns {Promise<Response>} Fetch response
 */
async function encryptedFetch(url, options = {}, encrypt = false) {
  if (encrypt && options.body && options.method === 'POST') {
    try {
      // Parse the JSON body
      const data = JSON.parse(options.body);

      // Encrypt and wrap the data
      const encryptedData = await dashboardCrypto.createEncryptedRequest(data);

      // Replace the body with encrypted version
      options.body = JSON.stringify(encryptedData);

    } catch (error) {
      console.error('Failed to encrypt request:', error);
      throw error;
    }
  }

  return fetch(url, options);
}

/**
 * Send encrypted command to server
 * @param {string} deviceId - Target device ID
 * @param {Object} command - Command object
 * @returns {Promise<Object>} Server response
 */
async function sendEncryptedCommand(deviceId, command) {
  const body = { deviceId: deviceId, ...command };

  const response = await encryptedFetch('/api/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }, true); // encrypt = true

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  return await response.json();
}

/**
 * Test encryption functionality
 * @returns {Promise<boolean>} True if test passes
 */
async function testEncryption() {
  try {
    console.log('Testing dashboard encryption...');

    const testData = {
      deviceId: 'test_device',
      action: 'setAngle',
      targetPosition: 90
    };

    // Test encryption
    const encrypted = await dashboardCrypto.encryptJSON(testData);
    console.log('Encrypted:', encrypted);

    // Test decryption
    const decrypted = await dashboardCrypto.decryptJSON(encrypted);
    console.log('Decrypted:', decrypted);

    // Verify data matches
    const matches = JSON.stringify(testData) === JSON.stringify(decrypted);
    console.log('Test result:', matches ? 'PASS' : 'FAIL');

    return matches;

  } catch (error) {
    console.error('Encryption test failed:', error);
    return false;
  }
}

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    DashboardCrypto,
    dashboardCrypto,
    encryptedFetch,
    sendEncryptedCommand,
    testEncryption
  };
}

/**
 * Usage Examples:
 *
 * // Test encryption
 * testEncryption();
 *
 * // Send encrypted command
 * sendEncryptedCommand('esp32_trash', { action: 'setAngle', targetPosition: 45 })
 *   .then(response => console.log('Command sent:', response))
 *   .catch(error => console.error('Command failed:', error));
 *
 * // Encrypt arbitrary data
 * dashboardCrypto.encryptJSON({ test: 'data' })
 *   .then(encrypted => console.log('Encrypted:', encrypted));
 *
 * // Use enhanced fetch with encryption
 * encryptedFetch('/api/command', {
 *   method: 'POST',
 *   headers: { 'Content-Type': 'application/json' },
 *   body: JSON.stringify({ deviceId: 'test', action: 'auto' })
 * }, true)
 *   .then(response => response.json())
 *   .then(data => console.log('Response:', data));
 */
