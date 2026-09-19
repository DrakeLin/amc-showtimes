// Exercise the real browser loading coordinator with an offline API and view.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/favorites.js', 'utf8').replace(/startShowtimes\(\);\s*$/, '');
const theaters = [{id: 101, name: 'NewPark'}, {id: 102, name: 'Mercado'}];
const movie = (name, date = '2026-09-19') => ({id: '8', title: 'Example', showings: [{theatre: name, date, cached_at: 100}]});
async function scenario({force = false, pending = [], metadata = [], newMetadata = false}) {
  const calls = [];
  const elements = new Map();
  const context = vm.createContext({console, setTimeout, $: id => {
    if (!elements.has(id)) elements.set(id, {classList: {add() {}, remove() {}}});
    return elements.get(id);
  }, setLoadingText() {}, setLoadingProgress() {}, setSubtitle() {}, mockApi: async (path, options) => {
    calls.push(path);
    if (path === '/api/showtimes') return {movies: theaters.map(t => movie(t.name)), theaters, dates: ['2026-09-19'], pending, metadata_pending: metadata};
    if (path === '/api/theater-week') {
      const theater = theaters.find(t => t.id === JSON.parse(options.body).theater);
      return {movies: [movie(theater.name, '2026-09-20')], cached_at: 200, metadata_pending: newMetadata};
    }
    if (path === '/api/metadata') return {metadata: {'8': {poster: 'poster', lb_rating: '4.2'}}, pending: 0};
    throw Error(`Unexpected request ${path}`);
  }});
  vm.runInContext(source + '\napi = mockApi; renderVisible = () => { visibleMovies = mergeVisible(visibleMovies); };', context);
  await vm.runInContext(`loadVercel(${force})`, context);
  assert.equal(elements.get('error').textContent, undefined);
  return {calls, movies: JSON.parse(vm.runInContext('JSON.stringify(visibleMovies)', context))};
}
(async () => {
  assert.deepEqual((await scenario({})).calls, ['/api/showtimes']);
  const refreshed = await scenario({force: true});
  assert.deepEqual(refreshed.calls, ['/api/showtimes', '/api/theater-week', '/api/theater-week']);
  assert.equal(refreshed.movies.length, 1);
  assert.equal(refreshed.movies[0].showings.length, 2);
  assert.ok(refreshed.movies[0].showings.every(s => s.date === '2026-09-20'));
  const partial = await scenario({pending: [{theater: 101}, {theater: 101}], newMetadata: true});
  assert.deepEqual(partial.calls, ['/api/showtimes', '/api/theater-week', '/api/metadata']);
  assert.equal(partial.movies[0].poster, 'poster');
  assert.equal(partial.movies[0].showings.find(s => s.theatre === 'Mercado').date, '2026-09-19');
  assert.deepEqual((await scenario({metadata: [102]})).calls, ['/api/showtimes', '/api/metadata']);
  console.log('4 frontend loading scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
